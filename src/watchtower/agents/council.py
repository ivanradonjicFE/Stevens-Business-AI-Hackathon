"""AI agent council: filter -> research -> market view -> critique -> level.

The council is the "thinking" layer on top of the deterministic pipeline.
Signals and clusters are facts; the council's job is to say which events
actually matter, what they would mean for the supply chain and the market,
and to argue with itself before anything is called a WATCH or a WARNING.

Three rules keep the demo honest:

* **Only new or escalating events** are analysed, so the model is spent
  where it adds signal rather than on every tick.
* **Every step falls back to a rule path.** With no API key, no network or
  no ``openai`` installed the council still produces a grounded read from
  the exposure table, the analog library and the evidence list, and says
  that it did.
* **The relevance number is never model-generated.** Every verdict carries
  the lexical relevance score and matched context tokens computed by
  :mod:`watchtower.relevance` from the market config, plus the gate that
  admitted the event, so a brief is reproducible with no API key. The model
  may widen the filter but not narrow it: an exclusion of an event the rules
  kept is overridden and the override is recorded.
* **Nothing here raises.** A failed model call is a normal outcome; the
  reporter falls back and the run continues.

``consider()`` is the only entry point used by the orchestrator. When a
model is reachable the work is dispatched to a small thread pool so a
burst of events does not serialise behind the feed; otherwise the fallback
runs inline (it is cheap) and tests stay deterministic.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

from watchtower.agents import llm
from watchtower.agents.analog import KIND_TO_SECTORS
from watchtower.config import MarketSpec
from watchtower.models import EventCluster, Severity
from watchtower.relevance import relevance_audit

log = logging.getLogger(__name__)

# Notification tiers, least to most urgent. Nothing below NOTIFY_MIN_LEVEL
# is surfaced as a notification; it stays a monitoring brief.
LEVELS = ("ADVISORY", "WATCH", "WARNING")
NOTIFY_MIN_LEVEL = "WATCH"

# One revision round: the analyst re-answers the critic once, then we stop.
MAX_CRITIC_ROUNDS = 1

# Lexical relevance (:mod:`watchtower.relevance`) feeds the filter as an
# auditable extra route: it is deterministic, never model-generated, and is
# reported next to the verdict whichever way the verdict goes. It is an
# *ordinal lexical overlap*, not a probability, so the gate sits mid-range of
# the 0.02-0.31 span observed on the replay corpus - deliberately permissive,
# because the model and the exposure table are what must be strict.
RELEVANCE_GATE = 0.15

#: Mean source credibility the severity route needs when no node is in range.
CREDIBILITY_GATE = 0.55

NODE_KINDS = (
    "fab_production",
    "materials",
    "equipment",
    "packaging",
    "logistics",
    "energy",
    "demand",
    "policy",
    "none",
)

# Evidence is capped so a long-running live event cannot blow the prompt.
EVIDENCE_LIMIT = 14

# A live model is spending someone's token budget, and free tiers are tight:
# Groq's free tier is 8k tokens/minute while one full analysis of an event
# costs roughly that (four stages, each re-sending its context). So the model
# path is rationed - it only analyses events at or above ``MODEL_MIN_SEVERITY``,
# runs at most ``MODEL_MAX_CONCURRENT`` at a time, and starts no more often than
# ``MODEL_MIN_INTERVAL_S`` - rather than queueing a backlog it cannot afford.
# The rule fallback is free, so it is deliberately *not* throttled: with no key
# every event still gets a read, inline.
MODEL_MIN_SEVERITY = Severity.ELEVATED
MODEL_MAX_CONCURRENT = 2
MODEL_MIN_INTERVAL_S = 40.0


@dataclass
class CouncilReport:
    """One event's finished council read, ready to render."""

    event_id: str
    level: str
    headline: str
    research: str
    mechanisms: list[tuple[str, str]]  # (node, description)
    market: str
    segments: list[tuple[str, str, str]]  # (segment, tickers, rationale)
    critique: str
    confidence: str
    evidence_quality: str
    revision: int
    model_used: bool
    trigger: str
    analyzed_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())
    # Auditable relevance provenance. ``lexical`` always comes from
    # :mod:`watchtower.relevance`, never from the model, so the number in the
    # brief can be reproduced from the config; ``route`` says which gate
    # admitted the event.
    lexical: float = 0.0
    lexical_matched: tuple[str, ...] = ()
    relevance_route: str = "none"
    relevance_reason: str = ""

    @property
    def notifiable(self) -> bool:
        """Would this tier actually notify (and is a model/model-fallback OK)?"""
        return LEVELS.index(self.level) >= LEVELS.index(NOTIFY_MIN_LEVEL)


# --- severity -> rule level (the AI can move this, with a reason) -----------


def rule_level(cluster: EventCluster) -> str:
    """Provisional tier from the auditable severity band."""
    if cluster.severity >= Severity.HIGH:
        return "WARNING"
    if cluster.severity >= Severity.ELEVATED:
        return "WATCH"
    return "ADVISORY"


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


# --- evidence ---------------------------------------------------------------


def evidence_rows(cluster: EventCluster, limit: int = EVIDENCE_LIMIT) -> list[dict]:
    """Numbered evidence, credible sources first.

    Hazards (feeds) are facts; wires are facts with an outlet; social is a
    rumour and is labelled as one. Sorting by credibility keeps the model
    anchored on the strongest material.
    """
    ordered = sorted(
        cluster.signals,
        key=lambda s: (s.source_type == "social", -s.credibility, s.ts),
    )
    rows = []
    for sig in ordered[:limit]:
        rows.append(
            {
                "source": sig.source_name,
                "type": sig.source_type,
                "date": datetime.fromtimestamp(sig.ts, UTC).strftime("%Y-%m-%d"),
                "text": sig.text.strip(),
                "credibility": round(sig.credibility, 2),
                "kind": sig.kind_hint or "unclassified",
            }
        )
    return rows


def _fmt_evidence(rows: list[dict]) -> str:
    return "\n".join(
        f"[{i + 1}] ({r['type']}, {r['source']}, {r['date']}, "
        f"credibility {r['credibility']:.2f}, kind {r['kind']}) {r['text']}"
        for i, r in enumerate(rows)
    )


def _exposure_table(cluster: EventCluster) -> str:
    if not cluster.exposure:
        return "none — no monitored supply-chain node in range"
    return "\n".join(
        f"- {node.name} ({node.kind}, criticality {node.criticality:.2f}): "
        f"exposure {score:.2f}"
        for node, score in cluster.exposure
    )


def _analog_table(cluster: EventCluster) -> str:
    if not cluster.analogs:
        return "none — no historical case file matched"
    lines = []
    for analog, score in cluster.analogs:
        lines.append(
            f"- {analog.name} ({analog.date}, {analog.duration_days}d, "
            f"match {score:.2f}, mechanisms {', '.join(analog.mechanisms)})"
        )
        for key, val in analog.market_reaction.items():
            lines.append(f"    {key}: {val}")
    return "\n".join(lines)


def _lexical(cluster: EventCluster, market: MarketSpec) -> dict:
    """Auditable lexical relevance: the score plus the tokens it hit.

    Thin wrapper over :func:`watchtower.relevance.relevance_audit` so the
    deterministic numbers are computed once and travel with the verdict
    instead of being re-derived by whoever reads the report.
    """
    audit = relevance_audit(cluster, market)
    matched = sorted({token for row in audit["rows"] for token in row["matched"]})
    return {
        "score": float(audit["scaled"]),
        "raw": float(audit["raw"]),
        "matched": tuple(matched[:10]),
        "cap_reached": bool(audit["cap_reached"]),
    }


def _lexical_block(lex: dict) -> str:
    """Prompt block for the lexical signal, caveat included."""
    hit = ", ".join(lex["matched"]) or "none"
    cap = "yes" if lex["cap_reached"] else "no"
    return (
        "LEXICAL RELEVANCE (deterministic - computed from the market "
        "watchlist and node vocabulary, not model-generated):\n"
        f"- score {lex['score']:.2f} of 1 (raw cosine {lex['raw']:.3f}, "
        f"x3 display scale), cap reached: {cap}\n"
        f"- market context tokens hit: {hit}\n"
        "- caveat: ordinal lexical overlap, blind to proximity - a severe "
        "event whose text names no market vocabulary scores 0.00 no matter "
        "how close it is to a node. Exposure, not this score, is the "
        "materiality test."
    )


def _context(cluster: EventCluster, market: MarketSpec) -> str:
    return (
        f"EVENT: {cluster.title}\n"
        f"Rule severity: {cluster.severity.name} "
        f"(score {cluster.severity_score:.2f})\n"
        f"First seen: {_stamp(cluster.first_seen)} | "
        f"Last seen: {_stamp(cluster.last_seen)} | "
        f"{len(cluster.signals)} signals, "
        f"{len({s.source_type for s in cluster.signals})} source types\n"
        f"Mechanism tags: {', '.join(sorted(cluster.mechanisms)) or 'none'}\n\n"
        f"{_lexical_block(_lexical(cluster, market))}\n\n"
        f"EXPOSED SUPPLY-CHAIN NODES:\n{_exposure_table(cluster)}\n\n"
        f"HISTORICAL PRECEDENTS:\n{_analog_table(cluster)}\n\n"
        f"EVIDENCE:\n{_fmt_evidence(evidence_rows(cluster))}"
    )


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%MZ")


# --- stage 1: relevance + credibility filter --------------------------------

FILTER_SYSTEM = """You are the triage analyst in an early-warning system for the SEMICONDUCTOR
supply chain: fabs in Taiwan/Korea/Japan/China/US/EU, materials and equipment,
packaging in SE Asia, and the ports, airports and straits chips move through.

Read the event's evidence and decide whether it is material to that supply chain.
- relevant: true if it threatens production, inputs, logistics, energy or chip
  demand at a monitored node, or is close enough to one to matter within days.
- credibility 0-1: how likely the event is real and current, judged from the
  source mix (official feeds and wires high; single social posts low).
- reason: one short sentence naming the mechanism and the node.
A deterministic LEXICAL RELEVANCE block is supplied: it only indicates whether
this market's vocabulary appears in the text, and is blind to distance, so use
it as a hint and not as materiality. A low lexical score on a severe event
near a node is expected and is not a reason to exclude it.
Be strict: market noise, opinion and events far from any monitored node are not relevant."""

FILTER_SCHEMA = llm.obj(
    {"relevant": llm.BOOL, "credibility": llm.NUM, "reason": llm.STR}
)


def _rule_filter(cluster: EventCluster, market: MarketSpec) -> dict:
    """Deterministic gate: exposure, then severity, then lexical overlap.

    The routes are ordered by how much they actually prove. Exposure to a
    monitored node is materiality; a high severity band on credible sources
    is a developing event; lexical overlap only says the text talks about
    this market. The lexical score is included in every branch so a verdict
    always carries the auditable number and the gate that produced it.
    """
    lex = _lexical(cluster, market)
    mean_cred = (
        sum(s.credibility for s in cluster.signals) / len(cluster.signals)
        if cluster.signals
        else 0.0
    )
    base = {
        "credibility": round(mean_cred, 2),
        "lexical": lex["score"],
        "lexical_matched": list(lex["matched"]),
        "lexical_cap": lex["cap_reached"],
    }
    if cluster.exposure:
        node, score = cluster.exposure[0]
        return base | {
            "relevant": True,
            "route": "exposure",
            "reason": (
                f"within exposure range of {node.name} (score {score:.2f}); "
                f"lexical relevance {lex['score']:.2f}"
            ),
        }
    if cluster.severity >= Severity.ELEVATED and mean_cred >= CREDIBILITY_GATE:
        return base | {
            "relevant": True,
            "route": "severity",
            "reason": (
                f"{cluster.severity.name} band on credible sources "
                f"(mean {mean_cred:.2f}) with no monitored node in range; "
                f"lexical relevance {lex['score']:.2f}"
            ),
        }
    if lex["score"] >= RELEVANCE_GATE:
        hit = ", ".join(lex["matched"]) or "generic market terms"
        return base | {
            "relevant": True,
            "route": "lexical",
            "reason": (
                f"no node in range and below the severity gate, but the text "
                f"overlaps the market context ({hit}) at lexical relevance "
                f"{lex['score']:.2f} >= {RELEVANCE_GATE}"
            ),
        }
    return base | {
        "relevant": False,
        "route": "none",
        "reason": (
            f"no monitored node in range, below the severity gate, and "
            f"lexical relevance {lex['score']:.2f} < {RELEVANCE_GATE}"
        ),
    }


def _filter(cluster: EventCluster, market: MarketSpec) -> dict:
    """Rule gate first, then the model; the model may widen but not narrow.

    The lexical score is deliberately taken from the rule verdict rather
    than the model, so the auditable number in a brief is reproducible from
    the market config alone. A model that drops an event the rules kept is
    overridden and the override is recorded, because missing a material
    shock is the expensive error here.
    """
    verdict = _rule_filter(cluster, market)
    result = llm.ask_json(
        FILTER_SYSTEM,
        _context(cluster, market),
        FILTER_SCHEMA,
        name="relevance",
        effort="low",
    )
    if not result:
        return verdict
    try:
        result["credibility"] = max(0.0, min(1.0, float(result["credibility"])))
    except (KeyError, TypeError, ValueError):
        result["credibility"] = verdict["credibility"]
    result["relevant"] = bool(result.get("relevant"))
    if verdict["relevant"] and not result["relevant"]:
        result["relevant"] = True
        result["overridden"] = (
            f"rule gate kept the event in scope ({verdict['route']} route); "
            "the model's exclusion was overridden"
        )
    # provenance travels with the verdict, never model-generated
    result["lexical"] = verdict["lexical"]
    result["lexical_matched"] = verdict["lexical_matched"]
    result["lexical_cap"] = verdict["lexical_cap"]
    result["route"] = verdict["route"]
    result["rule"] = verdict
    return result


# --- stage 2: research ------------------------------------------------------

RESEARCH_SYSTEM = """You are the research analyst in a semiconductor supply-chain early-warning
system. Using ONLY the numbered evidence (cite as [n]) plus general knowledge of
where chips are made and how they move, write a brief:
- summary: 2-3 sentences on what is happening and why it could matter for chips.
- confirmed: facts stated by credible sources, each ending with its [n].
- uncertain: unverified, sensational or inferred claims, and why.
- mechanisms: concrete routes from this event to the chip chain (grid outage ->
  tool shutdown and wafer scrap; port closure -> delayed equipment; flooding of a
  materials site), each with honest likelihood.
- watch_indicators: specific things that would confirm or refute escalation.
- evidence_quality: low/medium/high for the evidence base overall.
Do not invent facts, numbers or quotes. If the link to chips is weak, say so.
At most 4 confirmed, 3 uncertain, 4 mechanisms, 4 watch indicators."""

RESEARCH_SCHEMA = llm.obj(
    {
        "summary": llm.STR,
        "confirmed": llm.arr(llm.STR),
        "uncertain": llm.arr(llm.STR),
        "mechanisms": llm.arr(
            llm.obj(
                {
                    "node": llm.enum(*NODE_KINDS),
                    "description": llm.STR,
                    "likelihood": llm.enum("low", "medium", "high"),
                }
            )
        ),
        "watch_indicators": llm.arr(llm.STR),
        "evidence_quality": llm.enum("low", "medium", "high"),
    }
)


def _rule_research(cluster: EventCluster) -> dict:
    rows = evidence_rows(cluster)
    node = (
        f"{cluster.exposure[0][0].name} ({cluster.exposure[0][1]:.2f})"
        if cluster.exposure
        else "no monitored node"
    )
    analog = (
        f"{cluster.analogs[0][0].name} ({cluster.analogs[0][0].date})"
        if cluster.analogs
        else "none"
    )
    return {
        "summary": (
            f"{cluster.title} — {cluster.severity.name} band, "
            f"{len(cluster.signals)} signals across "
            f"{len({s.source_type for s in cluster.signals})} source types. "
            f"Nearest exposure {node}; closest precedent {analog}."
        ),
        "confirmed": [f"{r['text']} [{i + 1}]" for i, r in enumerate(rows[:3])],
        "uncertain": [
            "Rule-based brief: no model analysis available, so nothing here "
            "has been reconciled across sources."
        ],
        "mechanisms": [
            {
                "node": node_name,
                "description": "rule-derived exposure route",
                "likelihood": "low",
            }
            for node_name in _rule_mechanism_nodes(cluster)
        ],
        "watch_indicators": [],
        "evidence_quality": "low",
    }


def _rule_mechanism_nodes(cluster: EventCluster) -> list[str]:
    """Map exposed node kinds onto the mechanism vocabulary."""
    kinds = {node.kind for node, _ in cluster.exposure}
    if kinds & {"fab", "osat"}:
        return ["fab_production", "logistics"]
    if kinds & {"material"}:
        return ["materials", "logistics"]
    if kinds & {"canal", "strait", "port"}:
        return ["logistics"]
    return []


def _research(cluster: EventCluster, market: MarketSpec) -> dict:
    brief = llm.ask_json(
        RESEARCH_SYSTEM,
        _context(cluster, market),
        RESEARCH_SCHEMA,
        name="brief",
        effort="medium",
    )
    return brief if brief else _rule_research(cluster)


# --- stage 3: market analyst ------------------------------------------------

MARKET_SYSTEM = """You are the market analyst in a semiconductor supply-chain early-warning
system. Given the research brief, the historical precedents with their recorded
market reactions, and the exposed nodes, produce a market read.
- headline_view: one sentence on the expected market reaction.
- direction: negative / positive / mixed / negligible.
- segments: which chip segments and tickers are most exposed and why — use
  tickers from the given watchlist and segments, at most 3, one sentence each.
- base_case and risk_case: one or two sentences each.
- actionable: concrete things an investor, risk manager or supply-chain lead
  should do or watch now, at most 3, one sentence each.
Anchor every claim on a number or fact that appears in the input. If the link is
weak, say the impact is negligible rather than inventing severity."""

MARKET_SCHEMA = llm.obj(
    {
        "headline_view": llm.STR,
        "direction": llm.enum("negative", "positive", "mixed", "negligible"),
        "segments": llm.arr(
            llm.obj(
                {
                    "segment": llm.STR,
                    "tickers": llm.arr(llm.STR),
                    "direction": llm.enum("negative", "positive", "mixed", "negligible"),
                    "rationale": llm.STR,
                }
            )
        ),
        "base_case": llm.STR,
        "risk_case": llm.STR,
        "actionable": llm.arr(llm.STR),
        "confidence": llm.enum("low", "medium", "high"),
    }
)


def _rule_market(cluster: EventCluster, market: MarketSpec) -> dict:
    """Rule read: the precedent's recorded reaction, mapped onto this market."""
    if cluster.analogs:
        analog, score = cluster.analogs[0]
        reaction = analog.market_reaction
        direction = "negative"
        headline = (
            f"Closest precedent {analog.name} ({analog.date}, match {score:.2f}): "
            f"{reaction.get('index_move', 'no recorded market move')}"
        )
        base = (
            f"{analog.name} ran {analog.duration_days} days; "
            f"recorded lead-time effect: "
            f"{reaction.get('lead_time_delta', 'not recorded')}."
        )
    else:
        direction = "negligible"
        headline = "No close precedent in the library; treating the market read as negligible."
        base = "No precedent means no measured reaction to extrapolate from."

    sectors = {s for node, _ in cluster.exposure for s in KIND_TO_SECTORS.get(node.kind, set())}
    segments = []
    for name, tickers in _segment_pairs(market):
        if len(segments) >= 3:
            break
        rationale = (
            f"touched via {', '.join(sorted(sectors))}"
            if sectors
            else "no exposed node in range"
        )
        segments.append(
            {
                "segment": name,
                "tickers": list(tickers[:3]),
                "direction": "negative",
                "rationale": rationale,
            }
        )
    return {
        "headline_view": headline,
        "direction": direction,
        "segments": segments,
        "base_case": base,
        "risk_case": "Escalation to a WARNING would widen the exposure list.",
        "actionable": [
            "Confirm whether the exposed node is actually within the impact zone.",
            "Check inbound and outbound lead times at that node.",
        ],
        "confidence": "low",
    }


def _segment_pairs(market: MarketSpec) -> list[tuple[str, tuple[str, ...]]]:
    if market.segments:
        return list(market.segments)
    return [("watchlist", market.watchlist[:6])]


def _market_view(cluster: EventCluster, market: MarketSpec) -> dict:
    user = (
        f"{_context(cluster, market)}\n\n"
        f"RESEARCH BRIEF:\n{json.dumps(_research_text(cluster), ensure_ascii=False)}\n\n"
        f"MARKET: {market.label} (index {market.index})\n"
        f"WATCHLIST: {', '.join(market.watchlist)}\n"
        f"SEGMENTS: "
        + "; ".join(f"{name}: {', '.join(tk)}" for name, tk in _segment_pairs(market))
    )
    view = llm.ask_json(MARKET_SYSTEM, user, MARKET_SCHEMA, name="market_view", effort="medium")
    return view if view else _rule_market(cluster, market)


def _research_text(cluster: EventCluster) -> dict:
    """Compact research summary for the market prompt (avoids a second call)."""
    rows = evidence_rows(cluster)
    return {
        "title": cluster.title,
        "summary": rows[0]["text"] if rows else cluster.title,
        "sources": sorted({r["source"] for r in rows}),
    }


# --- stage 4: critic --------------------------------------------------------

CRITIC_SYSTEM = """You are the critic in a semiconductor supply-chain early-warning system.
Your job is to stop bad alerts. Check the research brief and market view against
the numbered evidence, the exposure table and the precedent numbers:
- claims not supported by evidence, invented numbers, overconfident magnitude;
- sensational sources driving conclusions, weak links to chips stated as likely;
- counter-evidence ignored (precedents disagree; no node actually in range);
- padding: an alert must be concise.
Return 'revise' only for material problems, each with a concrete fix; otherwise
'approve' with minor notes.
recommended_level: ADVISORY (weak or speculative link), WATCH (plausible and
developing), WARNING (credible, material, near-term). The rule level is given;
move it only with a reason in note."""

CRITIC_SCHEMA = llm.obj(
    {
        "verdict": llm.enum("approve", "revise"),
        "issues": llm.arr(
            llm.obj({"claim": llm.STR, "problem": llm.STR, "fix": llm.STR})
        ),
        "recommended_level": llm.enum(*LEVELS),
        "sensationalism_risk": llm.enum("low", "medium", "high"),
        "note": llm.STR,
    }
)


def _rule_critique(cluster: EventCluster, proposed: str, relevance: dict) -> dict:
    concern = ""
    if not cluster.exposure:
        concern = "No monitored supply-chain node is currently in exposure range."
    return {
        "verdict": "approve",
        "issues": [],
        "recommended_level": proposed,
        "sensationalism_risk": "medium",
        "note": (
            "Rule-based review (no model critique available); rule tier kept. "
            f"Admitted via the {relevance.get('route', 'none')} route at "
            f"lexical relevance {relevance.get('lexical', 0.0):.2f}. " + concern
        ).strip(),
        "relevance_reason": relevance.get("reason", ""),
    }


def _critique(
    cluster: EventCluster, market: MarketSpec, view: dict, proposed: str, relevance: dict
) -> dict:
    user = (
        f"{_context(cluster, market)}\n\n"
        f"RULE-PROPOSED LEVEL: {proposed}\n"
        f"RELEVANCE VERDICT: {json.dumps(relevance, ensure_ascii=False)}\n\n"
        f"MARKET VIEW:\n{json.dumps(view, ensure_ascii=False, default=str)}"
    )
    result = llm.ask_json(CRITIC_SYSTEM, user, CRITIC_SCHEMA, name="critique", effort="medium")
    return result if result else _rule_critique(cluster, proposed, relevance)


# --- assembly ---------------------------------------------------------------


def _rows_as_dicts(rows, *keys: str) -> list[dict]:
    """Normalise row lists to dicts.

    The strict model schemas always return objects, but a hand-written rule
    path may build tuples; normalising here keeps the assembly single-path
    and stops a fallback from crashing the report.
    """
    normalised: list[dict] = []
    for row in rows or []:
        if isinstance(row, dict):
            normalised.append(row)
            continue
        parts = tuple(row) if isinstance(row, (list, tuple)) else (row,)
        normalised.append(
            {key: parts[i] if i < len(parts) else "" for i, key in enumerate(keys)}
        )
    return normalised


def _render_view(view: dict) -> str:
    parts = [view.get("headline_view", "")]
    if view.get("base_case"):
        parts.append(view["base_case"])
    move = view.get("direction", "")
    parts.append(f"direction {move}, confidence {view.get('confidence', 'low')}.")
    return " ".join(p for p in parts if p).strip()


def _render_critique(critique: dict) -> str:
    note = critique.get("note", "")
    issues = critique.get("issues") or []
    if issues:
        listed = "; ".join(i.get("problem", "") for i in issues if i.get("problem"))
        return f"{note} ({len(issues)} issue(s): {listed})".strip()
    return note or "no material issues found."


class Council:
    """Per-market agent council, stateful over the event stream."""

    def __init__(
        self,
        market: MarketSpec,
        *,
        max_workers: int = 4,
        enabled: bool = True,
        min_severity: Severity = MODEL_MIN_SEVERITY,
        max_concurrent: int = MODEL_MAX_CONCURRENT,
        min_interval_s: float = MODEL_MIN_INTERVAL_S,
    ) -> None:
        self.market = market
        self.enabled = enabled
        # Rationing for the model path (see the constants above).
        self.min_severity = min_severity
        self.max_concurrent = max_concurrent
        self.min_interval_s = min_interval_s
        self.reports: dict[str, CouncilReport] = {}
        self.revision = 0  # bumped on every finished report (UI repaint signal)
        self._last_level: dict[str, str] = {}
        self._inflight: set[str] = set()
        self._last_model_start = 0.0
        self._skipped = 0  # events the rationing passed over, for the UI
        self._lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None
        if enabled and llm.available():
            self._pool = ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="council"
            )

    # --- scheduling ------------------------------------------------------

    def consider(self, cluster: EventCluster) -> CouncilReport | None:
        """Analyse a cluster if it is new or escalating; else do nothing.

        Returns the report when the fallback ran inline, or ``None`` when
        the work was dispatched to the pool (the report shows up in
        :attr:`reports` later). Never raises.
        """
        trigger = self._trigger(cluster)
        if trigger is None:
            return None
        if self._pool is None:
            return self._finish(self._analyze(cluster, trigger))
        # The rule fallback is free; a model call is not, so with a live model
        # the council only spends it on events that are already serious.
        if cluster.severity < self.min_severity:
            with self._lock:
                self._skipped += 1
            log.debug("council rationing passed over %s", cluster.event_id)
            return None
        with self._lock:
            if cluster.event_id in self._inflight:
                return None
            if not self._may_start_model_locked():
                self._skipped += 1
                log.debug("council rationing skipped %s", cluster.event_id)
                return None
            self._last_model_start = time.monotonic()
            self._inflight.add(cluster.event_id)
        self._pool.submit(self._dispatch, cluster, trigger)
        return None

    def _may_start_model_locked(self) -> bool:
        """May another model analysis start? Caller must hold ``_lock``."""
        if len(self._inflight) >= self.max_concurrent:
            return False
        return time.monotonic() - self._last_model_start >= self.min_interval_s

    def _dispatch(self, cluster: EventCluster, trigger: str) -> None:
        try:
            self._finish(self._analyze(cluster, trigger))
        except Exception:  # noqa: BLE001 - a council failure must not kill the feed
            log.exception("council failed for %s", cluster.event_id)
        finally:
            with self._lock:
                self._inflight.discard(cluster.event_id)

    def _finish(self, report: CouncilReport) -> CouncilReport:
        with self._lock:
            self.reports[report.event_id] = report
            self._last_level[report.event_id] = report.level
            self.revision += 1
        log.info(
            "council %s -> %s (%s, model=%s)",
            report.event_id,
            report.level,
            report.trigger,
            report.model_used,
        )
        return report

    def _trigger(self, cluster: EventCluster) -> str | None:
        """Why this cluster deserves an analysis, if it does."""
        if not self.enabled:
            return None
        if cluster.severity < Severity.GUARDED:
            return None
        level = rule_level(cluster)
        previous = self._last_level.get(cluster.event_id)
        if previous is None:
            return "new event"
        if _rank(level) > _rank(previous):
            return f"escalated {previous} -> {level}"
        return None

    # --- the pipeline ----------------------------------------------------

    def _analyze(self, cluster: EventCluster, trigger: str) -> CouncilReport:
        relevance = _filter(cluster, self.market)
        # the model answered the filter iff it wrote its own verdict back
        model_used = "rule" in relevance
        if not relevance.get("relevant"):
            return self._assemble(
                cluster, trigger, relevance, {}, {}, {}, model_used=False
            )

        brief = _research(cluster, self.market)
        view = _market_view(cluster, self.market)
        proposed = rule_level(cluster)
        critique = _critique(cluster, self.market, view, proposed, relevance)
        for _ in range(MAX_CRITIC_ROUNDS):
            if critique.get("verdict") != "revise":
                break
            issues = critique.get("issues") or []
            view = _market_view(cluster, self.market)  # revise: fresh read
            critique = _critique(cluster, self.market, view, proposed, relevance)
            if issues:
                critique.setdefault("revisions", len(issues))
        return self._assemble(cluster, trigger, relevance, brief, view, critique, model_used)

    def _assemble(
        self,
        cluster: EventCluster,
        trigger: str,
        relevance: dict,
        brief: dict,
        view: dict,
        critique: dict,
        model_used: bool,
    ) -> CouncilReport:
        level = critique.get("recommended_level") or rule_level(cluster)
        if level not in LEVELS:
            level = rule_level(cluster)
        if not relevance.get("relevant"):
            level = "ADVISORY"
        mechanisms = [
            (m.get("node", "none"), m.get("description", ""))
            for m in _rows_as_dicts(brief.get("mechanisms"), "node", "description")
        ]
        segments = [
            (
                s.get("segment", ""),
                ", ".join(
                    s["tickers"] if isinstance(s.get("tickers"), list) else []
                ),
                s.get("rationale", ""),
            )
            for s in _rows_as_dicts(
                view.get("segments"), "segment", "tickers", "rationale"
            )
        ]
        research = brief.get("summary") or relevance.get("reason", "") or cluster.title
        return CouncilReport(
            event_id=cluster.event_id,
            level=level,
            headline=view.get("headline_view") or relevance.get("reason", ""),
            research=research,
            mechanisms=mechanisms,
            market=_render_view(view) if view else relevance.get("reason", ""),
            segments=segments,
            critique=_render_critique(critique) if critique else "not reviewed",
            confidence=view.get("confidence", "low") if view else "low",
            evidence_quality=brief.get("evidence_quality", "low"),
            revision=self.revision,
            model_used=model_used,
            trigger=trigger,
            lexical=float(relevance.get("lexical", 0.0)),
            lexical_matched=tuple(relevance.get("lexical_matched") or ()),
            relevance_route=str(relevance.get("route", "none")),
            relevance_reason=str(relevance.get("reason", "")),
        )

    def analyse_now(self, cluster: EventCluster) -> CouncilReport | None:
        """Produce a read for this event right now, bypassing the rationing.

        The one-shot report runners use this. Rationing exists to stop a live
        feed booking more model work than a free tier can pay for, but a
        report asks about exactly one event, so the budget it protects should
        be spent on *that* event rather than on whichever event happened to
        arrive first. Runs synchronously, so the report never waits on a pool.
        """
        if not self.enabled:
            return None
        existing = self.reports.get(cluster.event_id)
        if existing is not None:
            return existing
        trigger = self._trigger(cluster)
        if trigger is None:
            return None
        return self._finish(self._analyze(cluster, trigger))

    def wait(self, timeout: float = 60.0, poll: float = 0.25) -> bool:
        """Block until every in-flight analysis has landed.

        The one-shot report runners (``scripts/sitrep.py``) need this: briefs
        arrive on the pool, so a report written straight after the feed would
        miss the read it just paid for. Returns True if it drained.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._inflight:
                    return True
            time.sleep(poll)
        with self._lock:
            return not self._inflight

    def close(self) -> None:
        """Shut the worker pool down (no-op on the inline rule path).

        Called when the dashboard swaps pipelines, so a restart does not
        leave a pool behind.
        """
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    # --- reads for the UI ------------------------------------------------

    @property
    def live(self) -> bool:
        """Is the model path in use (i.e. is there a worker pool to wait on)?"""
        return self._pool is not None

    def report_for(self, event_id: str) -> CouncilReport | None:
        return self.reports.get(event_id)

    def notifications(self) -> list[CouncilReport]:
        """Reports at or above the notify tier - the WATCH/WARNING set."""
        return [r for r in self.reports.values() if r.notifiable]

    def tally(self) -> str:
        """Compact level counts, e.g. ``1 WARNING / 2 WATCH``."""
        counts = {
            level: sum(1 for r in self.reports.values() if r.level == level)
            for level in reversed(LEVELS)
        }
        parts = [f"{n} {level}" for level, n in counts.items() if n]
        return " / ".join(parts) if parts else "no briefs"

    def status_line(self) -> str:
        """One line for the KPI strip: model state plus call accounting."""
        calls = llm.usage["calls"]
        failures = llm.usage["failures"]
        tokens = llm.usage["prompt_tokens"] + llm.usage["completion_tokens"]
        parts = [llm.status()]
        if calls:
            accounting = f"{calls} calls"
            if tokens:
                accounting += f"/{tokens} tok"
            if failures:
                accounting += f" ({failures} failed)"
            parts.append(accounting)
        elif failures:
            # every call failed (a dead key, no credits, no wifi): say so
            # rather than looking identical to "the model was never asked".
            parts.append(f"{failures} failed calls")
        if self._skipped:
            # events the token-budget rationing passed over, so a thin panel
            # is explained rather than looking like a broken council
            parts.append(f"{self._skipped} rationed")
        parts.append(f"{len(self.reports)} briefs")
        return " · ".join(parts)
