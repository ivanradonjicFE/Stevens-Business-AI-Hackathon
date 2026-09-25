"""Generate a point-in-time (PIT) SITREP from a replayed scenario.

Usage: uv run python scripts/sitrep.py <scenario> [--market semicon] [--pdf]

The report is dated to the scenario's as-of timestamp (last ingested
signal) — it describes only what the pipeline knows at that moment.
Scoring is framed as an insurance CAT model:

    CAT risk ~ Hazard x Exposure x Vulnerability

  - Hazard      = signal-derived severity composite (type/proximity/
                  corroboration/spread/escalation rubric)
  - Exposure    = chokepoint criticality in the event's footprint
                  (what insured value sits in harm's way)
  - Vulnerability = sector-resilience proxy per the market lens
                  (how fast the hit translates to insured loss)

Outlook is a *projection*: similarity-weighted insured-loss estimate
from analog case files, scaled by severity ratio (current / analog,
capped 0.33-1.5) — the same heuristic as the reference report.
Crossing the PCS $25M insured-loss threshold earns a CAT designation.
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
from collections import Counter
from datetime import UTC, datetime

from watchtower.agents.orchestrator import Orchestrator
from watchtower.config import REPLAY_DIR, SCENARIO_EXCLUSIONS, load_markets
from watchtower.models import Analog, EventCluster, Severity
from watchtower.sources import replay_signals

PCS_THRESHOLD_USD = 25_000_000

_REGION_BOXES = [
    ("middle_east", (10.0, 25.0, 45.0, 60.0)),
    ("europe", (35.0, -10.0, 65.0, 45.0)),
    ("east_asia", (20.0, 100.0, 50.0, 145.0)),
    ("latin_america", (-60.0, -120.0, 25.0, -30.0)),
    ("north_america", (25.0, -130.0, 60.0, -55.0)),
    ("southeast_asia", (-10.0, 95.0, 20.0, 125.0)),
]

# Vulnerability proxy per node kind: how fast physical disruption
# converts to insured loss. fab/material = tight coupling, single
# sources; canal/strait = delay-driven BI claims; port = cargo +
# business interruption.
_VULNERABILITY = {
    "fab": 0.85,
    "material": 0.8,
    "osat": 0.7,
    "canal": 0.6,
    "strait": 0.6,
    "port": 0.55,
}


def _as_of(event: EventCluster) -> str:
    return datetime.fromtimestamp(event.last_seen, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _region_of(event: EventCluster) -> str:
    if event.centroid is None:
        return "global"
    lat, lon = event.centroid
    for name, (la0, lo0, la1, lo1) in _REGION_BOXES:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return "global"


def _exposure_score(event: EventCluster) -> float:
    """Weighted exposure: sum of node exposure scores, capped at 1."""
    return min(1.0, sum(score for _, score in event.exposure[:3]))


def _vulnerability_score(event: EventCluster) -> float:
    """Exposure-weighted mean sector vulnerability of hit nodes."""
    if not event.exposure:
        return 0.4  # unknown assets -> mid prior
    total = sum(score for _, score in event.exposure)
    if not total:
        return 0.4
    return (
        sum(
            _VULNERABILITY.get(node.kind, 0.5) * score for node, score in event.exposure
        )
        / total
    )


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _cosine(a: Counter, b: Counter) -> float:
    num = sum(a[t] * b.get(t, 0) for t in a)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    return num / (da * db) if da and db else 0.0


def _cosine_relevance(event: EventCluster, market_key: str) -> float:
    """Cosine similarity between event text and market-context docs.

    Each signal is a document; the market context document is built from
    the vertical's watchlist, node kinds, mechanism vocabulary, and the
    names of every chokepoint node type in that vertical. The event's
    relevance is the credibility-weighted mean cosine of its top-10
    most-credible signals against the context doc.
    """
    spec = load_markets()[market_key]
    context_terms = (
        list(spec.watchlist)
        + sorted(spec.node_kinds)
        + sorted(spec.mechanisms)
        + spec.label.split()
    )
    # enrich: canonical vocabulary for each node kind
    vocab = {
        "fab": "semiconductor fab chip wafer foundry",
        "osat": "assembly test packaging",
        "material": "neon quartz substrate palladium gas",
        "canal": "canal shipping container vessel transit",
        "strait": "strait shipping tanker vessel transit",
        "port": "port cargo container shipping freight",
    }
    for kind in spec.node_kinds:
        context_terms += vocab.get(kind, "").split()
    ctx = Counter(_tokenize(" ".join(context_terms)))

    top = sorted(event.signals, key=lambda s: s.credibility, reverse=True)[:10]
    sims = [
        _cosine(Counter(_tokenize(s.text + " " + " ".join(s.entities))), ctx)
        for s in top
    ]
    wsum = sum(s.credibility for s in top) or 1.0
    rel = sum(sim * s.credibility for sim, s in zip(sims, top)) / wsum
    return round(min(1.0, rel * 3.0), 2)  # scale: raw TF cosine is small


def _parse_usd(text: str) -> float | None:
    """Pull a dollar amount out of a quantified-impact string.

    Handles ranges like "$2.5-3.5B" by taking the top of the range.
    """
    m = re.search(r"\$\s?([\d.]+)\s?(?:[-–]\s?([\d.]+))?\s?([BMKbmkm]?)", text)
    if not m:
        return None
    mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(3).upper()]
    top = m.group(2) or m.group(1)
    return float(top) * mult


# Prefer insured-loss figures over trade-flow figures when a case file
# quantifies both (e.g. Suez lists "$9.6B/day trade held" alongside the
# ~$916M GA claim — the latter is the insurance-relevant number).
_INSURED_KEYS = ("insur", "claim", "loss", "ga ", "liabilit", "payout")


def _analog_loss(analog: Analog) -> float | None:
    """Largest insured-dollar figure in the case file = loss proxy."""
    insured = {
        k: v
        for k, v in analog.quantified_impact.items()
        if any(t in k.lower() for t in _INSURED_KEYS)
        or any(t in v.lower() for t in _INSURED_KEYS)
    }
    pool = insured or analog.quantified_impact
    vals = [v for v in (_parse_usd(t) for t in pool.values()) if v]
    return max(vals) if vals else None


def _projected_loss(
    event: EventCluster, market_key: str
) -> tuple[float, list[tuple[Analog, float, float]]] | None:
    """Similarity-weighted mean of documented analog insured losses.

    No severity scaling: the estimate is the match-weighted mean of
    the real recorded losses. Every dollar traces to a named case
    file — nothing is inferred or extrapolated.
    """
    rows: list[tuple[Analog, float, float]] = []
    for analog, match in event.analogs:
        base = _analog_loss(analog)
        if base is None or match <= 0:
            continue
        rows.append((analog, match, base))
    if not rows:
        return None
    wsum = sum(m for _, m, _ in rows)
    est = sum(m * loss for _, m, loss in rows) / wsum
    return est, rows


def render(scenario: str, market_key: str, event: EventCluster) -> str:
    as_of = _as_of(event)
    hazard = event.severity_score
    exposure = _exposure_score(event)
    vulnerability = _vulnerability_score(event)
    projected = _projected_loss(event, market_key)

    L: list[str] = []
    L.append("# Supply-Chain Situation Report")
    L.append("")
    L.append(f"**Report type:** Point-in-time (PIT) — {as_of}")
    L.append(
        f"**Information cutoff:** {as_of}. This report is strictly "
        f"point-in-time: it reflects only signals received on or before "
        f"the cutoff. No post-cutoff data, hindsight, or future market "
        f"outcomes inform any score, projection, or precedent shown."
    )
    L.append("")
    L.append("---")
    L.append("")
    src = max(event.signals, key=lambda s: s.credibility)
    L.append(
        f"## [{event.severity.name}] {event.title} — "
        f"composite {event.severity_score:.2f}"
    )
    L.append("")
    L.append(f"**Lead source:** {src.source_name} ({src.source_type})")
    L.append(f"{src.url or 'no url'}")
    L.append("")
    L.append(
        f"**Corroboration:** {len(event.signals)} signals, "
        f"{len({s.source_type for s in event.signals})} source types | "
        f"**Region:** {_region_of(event)} | "
        f"**Market lens:** {market_key}"
    )
    L.append(
        f"**Relevance to {market_key}:** "
        f"{_cosine_relevance(event, market_key)} / 1 "
        f"(cosine similarity, signal text vs market context)"
    )
    L.append("")

    # --- Weather Disruption Index (WDI) -----------------------------
    # Six named factors, 0-10 each; two weighted sub-totals multiplied
    # into a 0-100 index. Bands: <30 GREEN, <60 ORANGE, else RED.
    #   Severity: intensity 0.4 / radius 0.2 / duration 0.4
    #   Vulnerability: concentration 0.4 / buffers 0.2 / utility 0.4
    comp = {c.name: c.value for c in event.components}
    intensity = comp.get("event_type", 0.0) * 10
    radius = comp.get("spread", 0.0) * 10
    duration = comp.get("escalation", 0.0) * 10
    sev_score = 0.4 * intensity + 0.2 * radius + 0.4 * duration

    concentration = min(10.0, exposure * 10)
    # buffers = inverse of node vulnerability (high-vuln assets hold
    # thinner slack); utility dep = vulnerability itself (fab > port)
    utility_dep = vulnerability * 10
    buffers = max(0.0, 10.0 - utility_dep)
    vul_score = 0.4 * concentration + 0.2 * buffers + 0.4 * utility_dep

    wdi = sev_score * vul_score
    band = "GREEN" if wdi < 30 else "ORANGE" if wdi < 60 else "RED"

    L.append("### Weather Disruption Index (WDI)")
    L.append("")
    L.append(f"**{wdi:.0f} / 100 — {band}**")
    L.append("")
    L.append("| Block | Factor | Raw (0-10) | Weight |")
    L.append("|---|---|---|---|")
    L.append(f"| Severity | intensity | {intensity:.1f} | 0.40 |")
    L.append(f"| Severity | radius | {radius:.1f} | 0.20 |")
    L.append(f"| Severity | duration | {duration:.1f} | 0.40 |")
    L.append(f"| **Severity total** | | **{sev_score:.1f}** | |")
    L.append(f"| Vulnerability | concentration | {concentration:.1f} | 0.40 |")
    L.append(f"| Vulnerability | inventory buffers | {buffers:.1f} | 0.20 |")
    L.append(f"| Vulnerability | utility dependency | {utility_dep:.1f} | 0.40 |")
    L.append(f"| **Vulnerability total** | | **{vul_score:.1f}** | |")
    L.append(
        f"| **WDI = severity × vulnerability** | "
        f"**{sev_score:.1f} × {vul_score:.1f}** | **{wdi:.0f}** | — |"
    )
    L.append("")

    # --- precedent table (PIT-safe: no post-event market moves) ------
    L.append("### Historical precedents")
    L.append("")
    L.append("| Event | Date | Match | Documented insured impact | Weight |")
    L.append("|---|---|---|---|---|")
    for analog, match in event.analogs:
        # show the same insured-loss figure the estimate is scaled from
        doc_raw = _analog_loss(analog)
        doc = f"${doc_raw / 1e6:,.0f}M" if doc_raw else "—"
        L.append(
            f"| {analog.name} | {analog.date} | {match:.2f} | "
            f"{doc} | w={match:.2f} |"
        )
    L.append("")

    # --- outlook ----------------------------------------------------
    if projected:
        est, rows = projected
        lo = min(loss for _, _, loss in rows)
        hi = max(loss for _, _, loss in rows)
        L.append("### Projected insured impact")
        L.append("")
        pcs = (
            "**EXCEEDS** the PCS $25M catastrophe-designation threshold"
            if est >= PCS_THRESHOLD_USD
            else "below the PCS $25M catastrophe-designation threshold"
        )
        L.append(
            f"Match-weighted mean of documented analog losses: "
            f"**${est / 1e6:,.0f}M** "
            f"(precedent range ${lo / 1e6:,.0f}M–${hi / 1e6:,.0f}M; "
            f"each weight is the row's match score above). {pcs}."
        )
        L.append("")
    else:
        L.append("### Projected insured impact")
        L.append("")
        L.append(
            "No quantified-loss precedent — monitor for escalation; "
            "designation cannot be estimated from current analogs."
        )
        L.append("")

    # --- insurer actions -------------------------------------------
    L.append("### Recommended insurer actions")
    L.append("")
    if event.severity >= Severity.HIGH:
        L.append(
            "- **Declare CAT readiness now:** initiate PCS-style "
            "catastrophe tracking for the affected region — spin up a "
            "dedicated claims serial number and reserve committee "
            "review before policyholder volume spikes."
        )
        L.append(
            "- **Claims capacity:** pre-stage adjusters and surge "
            "call-center staffing for the footprint; expect first "
            "notices of loss within 24–72h of the exposure event."
        )
        L.append(
            "- **Underwriting hold:** pause new-binding authority in "
            "the affected geography/sector pending loss confirmation."
        )
    else:
        L.append(
            "- **Monitor:** hold new-binding review in the affected "
            "region; no claims surge expected at current severity, but "
            "the corroboration trend justifies a daily re-score."
        )
    if projected and est >= PCS_THRESHOLD_USD:
        L.append(
            f"- **Reinsurance notification:** projected "
            f"${est / 1e6:,.0f}M clears the PCS $25M designation line — "
            f"notify reinsurance partners and verify attachment-point "
            f"availability for the exposed portfolio."
        )
    else:
        L.append(
            "- **Reinsurance:** projected exposure below the PCS $25M "
            "catastrophe-designation threshold; standard retention "
            "expected to absorb — no notification required yet."
        )
    exposed = ", ".join(n.name for n, _ in event.exposure[:3]) or "the affected region"
    L.append(
        f"- **Portfolio triage:** proximity-ranked watchlist — "
        f"{exposed}. Pull exposure reports and flag concentration above "
        f"retention thresholds for proactive outreach/mitigation."
    )
    L.append("")

    # --- footer -----------------------------------------------------
    L.append("### How we scored this")
    L.append("")
    L.append(
        f"- {len(event.signals)} signals from "
        f"{len({s.source_name for s in event.signals})} distinct sources; "
        f"hazard composite {hazard:.2f} via rubric "
        f"(type/proximity/corroboration/spread/escalation)."
    )
    L.append(
        "- Exposure = distance-decayed chokepoint criticality; "
        "vulnerability = sector-resilience prior per node kind."
    )
    L.append(
        "- Analogs picked by mechanism/sector/geo similarity; projected "
        "loss = match-weighted mean of documented analog losses, scaled "
        "by severity ratio (capped 0.33–1.5×)."
    )
    L.append(
        "- Caveat: automated early-warning signal, not underwriting "
        "advice. Replay data is curated and point-in-time; projections "
        "are analog-derived estimates, not observed outcomes."
    )
    L.append("")
    return "\n".join(L)


def main() -> None:
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", default="")
    parser.add_argument("--market", default="semicon")
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--out", default="sitreps")
    parser.add_argument(
        "--source",
        choices=["gdelt", "eonet"],
        default="gdelt",
        help="live source: gdelt news (needs --live query) or eonet natural events",
    )
    parser.add_argument(
        "--live",
        metavar="QUERY",
        help="pull PIT news from GDELT instead of a replay file",
    )
    parser.add_argument(
        "--asof",
        metavar="YYYYMMDDHHMMSS",
        help="point-in-time cutoff for --live (UTC)",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=3,
        help="look-back window before --asof for --live",
    )
    args = parser.parse_args()

    market = load_markets()[args.market]
    orch = Orchestrator(
        market,
        exclude_analogs=SCENARIO_EXCLUSIONS.get(args.scenario, frozenset()),
    )

    if args.live or args.source == "eonet":
        if not args.asof:
            raise SystemExit("--live/--source eonet requires --asof YYYYMMDDHHMMSS")
        from datetime import datetime as dt
        from datetime import timedelta

        end = args.asof
        start_dt = dt.strptime(end, "%Y%m%d%H%M%S")
        start = (start_dt - timedelta(days=args.window_days)).strftime("%Y%m%d%H%M%S")
        if args.source == "eonet":
            from watchtower.sources import eonet_signals

            start_iso = (start_dt - timedelta(days=args.window_days)).strftime(
                "%Y-%m-%d"
            )
            end_iso = start_dt.strftime("%Y-%m-%d")
            signals = list(eonet_signals(start_iso, end_iso, limit=100))
            print(f"eonet: {len(signals)} PIT signals ({start_iso}..{end_iso})")
            args.scenario = args.scenario or "live_eonet"
        else:
            from watchtower.sources import gdelt_signals

            signals = list(gdelt_signals(args.live, start, end, max_records=100))
            print(f"gdelt: {len(signals)} PIT signals ({start}..{end})")
            args.scenario = args.scenario or f"live_{args.live[:20]}"
    else:
        if not args.scenario:
            raise SystemExit("scenario required in replay mode")
        signals = list(replay_signals(REPLAY_DIR / f"{args.scenario}.jsonl"))
    for signal in signals:
        orch.process(signal)

    events = orch.correlator.active_events()
    if not events:
        raise SystemExit(f"no events formed for {args.scenario}")
    top = max(events, key=lambda e: e.severity_score)

    md = render(args.scenario, args.market, top)
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"sitrep_{args.scenario}.md"
    out.write_text(md)
    print(f"wrote {out}")

    if args.pdf:
        if not shutil.which("pandoc"):
            print("pandoc not found; skipping pdf")
            return
        pdf = out.with_suffix(".pdf")
        subprocess.run(
            ["pandoc", str(out), "-o", str(pdf), "-V", "geometry:margin=1in"],
            check=True,
        )
        print(f"wrote {pdf}")


if __name__ == "__main__":
    main()
