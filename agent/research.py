"""Research agent: dig into one event. It plans a targeted follow-up news search, runs it, then writes a
brief that separates what is confirmed from what is speculation and maps how the event reaches the chip supply chain."""
import re

import collect
import config
import llm
import store
from agents import Agent

MECH_NODES = ["fabs", "materials", "equipment", "packaging", "logistics", "energy", "demand", "policy"]
SEGMENTS = ["foundry", "memory", "equipment", "idm", "fabless", "packaging", "shipping"]


def evidence(event_id: str, extra: list[dict] | None = None, limit: int = 30) -> list[dict]:
    """Numbered evidence list shared by research, analyst and critic: hazard feeds first, then credible news."""
    sigs = store.rows("SELECT * FROM signals WHERE event_id=?", (event_id,))
    sigs.sort(key=lambda s: (s["data"]["kind"] == "NEWS", -s["triage"].get("credibility", 0.5), s["data"]["time"]))
    items = []
    for s in sigs[:limit]:
        d, t = s["data"], s["triage"]
        dom = d["articles"][0]["domain"] if d.get("articles") else d["source"]
        items.append({"source": dom, "date": d["time"][:10], "title": d["title"], "url": d["url"],
                      "credibility": t.get("credibility"), "sensational": t.get("sensational", False),
                      "kind": "hazard feed" if d["kind"] != "NEWS" else "news"})
    for a in extra or []:
        items.append({"source": a["domain"], "date": a["seendate"][:8], "title": a["title"], "url": a["url"],
                      "credibility": None, "sensational": False, "kind": "follow-up search"})
    return items


def fmt_evidence(items: list[dict]) -> str:
    return "\n".join(f"[{i + 1}] ({e['kind']}, {e['source']}, {e['date']}"
                     + (f", credibility {e['credibility']:.2f}" if e["credibility"] is not None else "")
                     + (", SENSATIONAL" if e["sensational"] else "") + f") {e['title']}"
                     for i, e in enumerate(items))


PLAN_SCHEMA = llm.obj({"must_terms": llm.arr(llm.STR), "impact_terms": llm.arr(llm.STR), "why": llm.STR})
PLAN_SYSTEM = """You plan ONE follow-up news search to learn whether an event is hitting the semiconductor supply chain.
Return 2-4 short must_terms that identify the event (storm name without year suffix, place, country) and 3-5
impact_terms about chip-industry consequences (e.g. TSMC, semiconductor, fab, port, airport, power outage).
Single words or 2-word phrases, no boolean operators, no dashes or other punctuation."""

BRIEF_SCHEMA = llm.obj({
    "summary": llm.STR,
    "confirmed": llm.arr(llm.STR),
    "uncertain": llm.arr(llm.STR),
    "mechanisms": llm.arr(llm.obj({"node": llm.enum(*MECH_NODES), "description": llm.STR,
                                   "likelihood": llm.enum("low", "medium", "high")})),
    "affected_segments": llm.arr(llm.enum(*SEGMENTS)),
    "time_horizon": llm.enum("days", "weeks", "months"),
    "watch_indicators": llm.arr(llm.STR),
    "evidence_quality": llm.enum("low", "medium", "high"),
})
BRIEF_SYSTEM = """You are the research analyst in a semiconductor supply-chain early-warning system.
Using ONLY the numbered evidence (cite as [n]) plus general industry knowledge about where chips are made and how
they move, write a research brief:
- summary: 2-3 sentences on what is happening and why it could matter for chips
- confirmed: facts stated by credible sources, each with [n]
- uncertain: claims that are unverified, sensational, or inferred - say why
- mechanisms: concrete ways this storm/tsunami reaches the chip supply chain (e.g. grid outage -> fab tool shutdown
  and wafer scrap; port/airport closure -> delayed equipment and chip shipments; flooding of a materials site),
  with honest likelihood; use the critical sites in the impact zone and their distances
- watch_indicators: specific things that would confirm or refute escalation
Do not invent facts, numbers or quotes. If the link to chips is weak, say so.
Be concise: at most 5 confirmed, 4 uncertain, 4 mechanisms (<= 2 sentences each), 5 watch_indicators (one line each)."""


class ResearchAgent(Agent):
    name = "Research"

    def run(self, ev: dict) -> dict:
        items = evidence(ev["id"])
        extra, query = [], None
        plan = llm.ask_json(PLAN_SYSTEM, f"Event: {ev['title']}\nCategory: {ev['category']}, region: {ev['region']}\n"
                            f"Evidence:\n{fmt_evidence(items[:12])}", PLAN_SCHEMA, "plan", model=config.FAST_MODEL, effort="low")
        if plan and plan["must_terms"] and plan["impact_terms"]:
            clean = lambda t: re.sub(r"[^\w\s]", " ", t).strip()  # GDELT rejects dashes/punctuation
            q = lambda terms: "(" + " OR ".join(f'"{c}"' if " " in c else c for c in map(clean, terms[:5]) if c) + ")"
            query = f"{q(plan['must_terms'][:4])} {q(plan['impact_terms'][:5])} sourcelang:english"
            try:
                extra = [a for a in collect._gdelt(query) if a["title"].strip().lower()
                         not in {e["title"].lower() for e in items}][:12]
            except collect.Throttled:
                extra = []
            self.log("follow-up search", f"{query} -> {len(extra)} new headlines", ev["id"])
        items = evidence(ev["id"], extra)

        brief = llm.ask_json(BRIEF_SYSTEM, f"Event: {ev['metrics'].get('label', ev['title'])} ({ev['title']})\nCategory: {ev['category']}, region: {ev['region']}\n"
                             f"Scoring notes: {ev['metrics']['notes']}\nCritical sites in impact zone: "
                             f"{ev['metrics'].get('exposed') or 'n/a (news event)'}\n\nEvidence:\n{fmt_evidence(items)}",
                             BRIEF_SCHEMA, "brief", effort="medium")
        if not brief:  # rule fallback
            brief = {"summary": f"{ev['title']} ({ev['category'].replace('_', ' ')}, {ev['region']}).",
                     "confirmed": [f"{e['title']} [{i + 1}]" for i, e in enumerate(items[:3])],
                     "uncertain": ["Automated rule-based brief: no model analysis available."],
                     "mechanisms": [], "affected_segments": [], "time_horizon": "weeks",
                     "watch_indicators": [], "evidence_quality": "low", "fallback": True}
        self.log("brief written", f"{len(brief['confirmed'])} confirmed / {len(brief['uncertain'])} uncertain, "
                                  f"evidence quality {brief['evidence_quality']}", ev["id"])
        return {"brief": brief, "evidence": items, "followup_query": query}
