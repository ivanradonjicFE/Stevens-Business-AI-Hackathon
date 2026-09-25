"""Market analyst agent: turn the research brief + historical precedents + live market data into an expected
market impact for semiconductor segments. Numbers come from code (analogs.py); the model interprets them."""
import json
from datetime import datetime
from types import SimpleNamespace

import llm
from agents import Agent
from agents.research import SEGMENTS
from alert import extrapolate

VIEW_SCHEMA = llm.obj({
    "headline_view": llm.STR,
    "direction": llm.enum("negative", "positive", "mixed", "negligible"),
    "expected_sox_move": llm.obj({"low": llm.NUM, "base": llm.NUM, "high": llm.NUM}),
    "horizon": llm.enum("days", "weeks", "months"),
    "segments": llm.arr(llm.obj({"segment": llm.enum(*SEGMENTS), "tickers": llm.arr(llm.STR),
                                 "direction": llm.enum("negative", "positive", "mixed", "negligible"),
                                 "rationale": llm.STR})),
    "base_case": llm.STR,
    "risk_case": llm.STR,
    "actionable": llm.arr(llm.STR),
    "confidence": llm.enum("low", "medium", "high"),
})
SYSTEM = """You are the market analyst in a semiconductor supply-chain early-warning system.
Given a research brief, historical precedents with their MEASURED market reactions (semiconductor index SOX vs S&P 500,
in %), their supply/demand shocks, the rule-based projection and the CURRENT market reaction, produce a market impact view.
Rules:
- expected_sox_move is the 20-trading-day SOX move vs S&P 500 in %. Anchor it on the projection and precedents; you
  may shift it for differences you can justify from the brief, and say so in base_case.
- Only use numbers that appear in the input. Never invent prices, percentages or dates.
- segments: which chip segments/tickers are most exposed (use tickers from the data: TSM, MU, ASML, INTC, ZIM, or
  well-known ones you are sure fit, e.g. Samsung for memory), and why.
- actionable: concrete, specific things an investor, risk manager or supply-chain lead should do or watch now.
- If current market moves are far larger than precedents, say other drivers dominate.
- confidence reflects evidence quality and how consistent the precedents are.
Be concise - this feeds an alert: at most 4 segments (each with at least one ticker, rationale ONE sentence <= 15 words),
base_case and risk_case <= 3 sentences each, at most 4 actionable items of one sentence each."""


def _analog_table(ext: dict) -> str:
    rows = []
    for an in ext["analogs"]:
        sox = an["reaction"].get("^SOX", {})
        sh = an.get("shocks", {})
        rows.append({"event": an["name"], "date": an["date"], "similarity": an["similarity"], "severity": an["severity"],
                     "sox_vs_spx_pct": {"+1d": sox.get(1), "+5d": sox.get(5), "+20d": sox.get(20)},
                     "supply_shocks": sh.get("supply", []), "demand_shocks": sh.get("demand", [])})
    return json.dumps(rows, indent=1, ensure_ascii=False)


class MarketAnalystAgent(Agent):
    name = "MarketAnalyst"

    def run(self, ev: dict, research: dict, critique: dict | None = None, previous: dict | None = None) -> dict:
        m = ev["metrics"]
        a = SimpleNamespace(category=ev["category"], region=ev["region"], severity=m["severity"],
                            event=SimpleNamespace(time=datetime.fromisoformat(m["start"])))
        ext = research.get("_ext") or extrapolate(a)
        research["_ext"] = ext  # reuse on revision: precedents and prices don't change
        cur = ext["current"]
        user = (f"EVENT: {ev['title']} ({ev['category']}, {ev['region']}, severity {m['severity']}/3)\n\n"
                f"RESEARCH BRIEF:\n{json.dumps(research['brief'], indent=1, ensure_ascii=False)}\n\n"
                f"PRECEDENTS:\n{_analog_table(ext)}\n\n"
                f"RULE-BASED PROJECTION (20d SOX vs S&P %): {json.dumps(ext['projection_sox_excess'])} "
                f"(confidence {ext['confidence']}); rule read: {ext['market_read']}\n\n"
                f"CURRENT REACTION vs S&P % (as of {cur['as_of']}; since_event capped at 20 trading days):\n"
                f"{json.dumps({k: v for k, v in cur.items() if k not in ('as_of', 'event_window_days')}, indent=1)}")
        if critique:
            user += (f"\n\nYOUR PREVIOUS VIEW:\n{json.dumps(previous, indent=1)}\n\nCRITIC'S OBJECTIONS - address every one:\n"
                     f"{json.dumps(critique['issues'], indent=1)}")
        view = llm.ask_json(SYSTEM, user, VIEW_SCHEMA, "market_view", effort="medium")
        if not view:
            p = ext["projection_sox_excess"].get(20, {"low": 0, "expected": 0, "high": 0})
            view = {"headline_view": ext["market_read"], "direction": "negative" if p["expected"] < -1 else "negligible",
                    "expected_sox_move": {"low": p["low"], "base": p["expected"], "high": p["high"]},
                    "horizon": "weeks", "segments": [], "base_case": ext["market_read"], "risk_case": "",
                    "actionable": [], "confidence": ext["confidence"], "fallback": True}
        self.log("revised view" if critique else "market view",
                 f"{view['direction']}, SOX {view['expected_sox_move']['base']:+.1f}% (range "
                 f"{view['expected_sox_move']['low']:+.1f} to {view['expected_sox_move']['high']:+.1f}), "
                 f"confidence {view['confidence']}", ev["id"])
        return view
