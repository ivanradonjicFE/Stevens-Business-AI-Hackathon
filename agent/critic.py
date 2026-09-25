"""Critic agent: red-team the research brief and market view against the evidence before anything is sent."""
import json

import config
import llm
from agents import Agent
from agents.research import fmt_evidence

SCHEMA = llm.obj({
    "verdict": llm.enum("approve", "revise"),
    "issues": llm.arr(llm.obj({"claim": llm.STR, "problem": llm.STR, "fix": llm.STR})),
    "confidence": llm.enum("low", "medium", "high"),
    "recommended_level": llm.enum(*config.LEVELS),
    "sensationalism_risk": llm.enum("low", "medium", "high"),
    "note": llm.STR,
})
SYSTEM = """You are the critic in a semiconductor supply-chain early-warning system. Your job is to stop bad alerts.
Check the research brief and market view against the numbered evidence and the precedent numbers:
- claims not supported by evidence, invented numbers, overconfident direction or magnitude
- sensational sources driving conclusions; weak or speculative links to chips presented as likely
- ignored counter-evidence (e.g. precedents disagree, market already moved for other reasons)
Judge numbers against ALL the data given (precedents, rule projection, per-ticker current reaction) before calling
them invented. Also flag padding: an alert must be concise.
Return 'revise' only for material problems (list each with a concrete fix); otherwise 'approve' with minor notes.
recommended_level: ADVISORY (weak/speculative link), WATCH (plausible, developing), WARNING (credible, material, near-term).
Proposed level from the scoring rules is given; move it only with a reason in note."""


class CriticAgent(Agent):
    name = "Critic"

    def run(self, ev: dict, research: dict, view: dict) -> dict:
        ext = research["_ext"]
        precedents = [{"event": a["name"], "sox_+20d": a["reaction"].get("^SOX", {}).get(20)} for a in ext["analogs"]]
        user = (f"EVENT: {ev['title']} | rule-proposed level {ev['metrics']['level']} | scoring notes: {ev['metrics']['notes']}\n\n"
                f"EVIDENCE:\n{fmt_evidence(research['evidence'])}\n\n"
                f"RESEARCH BRIEF:\n{json.dumps(research['brief'], indent=1, ensure_ascii=False)}\n\n"
                f"PRECEDENTS (SOX vs S&P, % at +20d): {json.dumps(precedents)}\n"
                f"RULE-BASED PROJECTION (20d SOX vs S&P %): {json.dumps(ext['projection_sox_excess'])}\n"
                f"CURRENT REACTION vs S&P % by ticker (since_event capped at 20 trading days): "
                f"{json.dumps({k: v for k, v in ext['current'].items() if k not in ('as_of', 'event_window_days')})}\n\n"
                f"MARKET VIEW:\n{json.dumps(view, indent=1, ensure_ascii=False)}")
        res = llm.ask_json(SYSTEM, user, SCHEMA, "critique", effort="medium")
        if not res:
            res = {"verdict": "approve", "issues": [], "confidence": view.get("confidence", "low"),
                   "recommended_level": ev["metrics"]["level"], "sensationalism_risk": "medium",
                   "note": "No model critique available; rule-based level kept.", "fallback": True}
        self.log(res["verdict"], f"{len(res['issues'])} issues, level {res['recommended_level']}, "
                                 f"confidence {res['confidence']}, sensationalism {res['sensationalism_risk']}", ev["id"])
        return res
