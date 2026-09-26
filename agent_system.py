"""Always-on multi-agent research system for semiconductor supply-chain risk.

    python agent_system.py              # run forever (Ctrl+C to stop)
    python agent_system.py --once       # one full cycle, then write + open a run report PDF
    python agent_system.py --days 90    # hazard lookback for the demo
    python agent_system.py --reset      # forget all state (signals, events, notifications) first

Each cycle:  scouts -> triage -> correlator -> [research -> market analyst <-> critic -> notifier] per event
"""
import argparse
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import config  # noqa: F401  (sets SSL certs)
import analogs
import llm
import store
from agents.analyst import MarketAnalystAgent
from agents.correlator import CorrelatorAgent, needs_analysis
from agents.critic import CriticAgent
from agents.notifier import NotifierAgent, write_run_report, write_status_board
from agents.research import ResearchAgent
from agents.scouts import HazardScout, NewsScout
from agents.triage import TriageAgent

scouts = [HazardScout(), NewsScout()]
triage, correlator = TriageAgent(), CorrelatorAgent()
research, analyst, critic, notifier = ResearchAgent(), MarketAnalystAgent(), CriticAgent(), NotifierAgent()


def analyze(ev: dict):
    """Deep-research pipeline for one event, with a critic-driven revision loop."""
    try:
        r = research.run(ev)
        view = analyst.run(ev, r)
        crit = critic.run(ev, r, view)
        critiques = [crit]
        for _ in range(config.MAX_CRITIC_ROUNDS):
            if crit["verdict"] != "revise":
                break
            view = analyst.run(ev, r, critique=crit, previous=view)
            crit = critic.run(ev, r, view)
            critiques.append(crit)
        ext = r["_ext"]
        ev["analysis"] = {"brief": r["brief"], "market_view": view, "critiques": critiques,
                          "final_level": crit["recommended_level"], "followup_query": r["followup_query"],
                          "evidence": r["evidence"], "analyzed_at": store.now(),
                          "precedents": [{"name": a["name"], "date": a["date"], "sox": a["reaction"].get("^SOX")}
                                         for a in ext["analogs"]]}
        ev["analyzed_metrics"] = ev["metrics"]
        store.save_event(ev)
        notifier.run(ev, r, view, crit, critiques)
    except Exception:
        store.log("Orchestrator", "analysis failed", traceback.format_exc()[-1500:], ev["id"])
        print(f"  [Orchestrator] analysis failed for {ev['id']}:\n{traceback.format_exc()[-800:]}")


def cycle(n: int):
    t0 = time.time()
    print(f"\n=== cycle {n} - {datetime.now():%H:%M:%S} ===", flush=True)
    for s in scouts:
        if s.due():
            try:
                s.run()
            except Exception as ex:
                s.mark()
                s.log("failed", f"{type(ex).__name__}: {ex}")
    triage.run()
    correlator.run()

    active = store.rows("SELECT * FROM events WHERE status='active'")
    todo = sorted((e for e in active if needs_analysis(e)), key=lambda e: -e["metrics"]["relevance"])
    todo = todo[:config.MAX_EVENTS_ANALYZED_PER_CYCLE]
    if todo:
        print(f"  [Orchestrator] deep research on {len(todo)} event(s): " + "; ".join(e["metrics"].get("label", e["title"][:50]) for e in todo))
        analogs.prices()  # warm the price cache once before threads start
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            list(pool.map(analyze, todo))
    write_status_board()
    u = llm.usage
    print(f"  [Orchestrator] cycle done in {time.time() - t0:.0f}s | {len(active)} active events | "
          f"LLM calls {u['calls']} ({u['failures']} failed), tokens in/out {u['prompt_tokens']}/{u['completion_tokens']} "
          f"| status board: out/status.md + out/status.pdf", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--days", type=int, help="hazard lookback in days")
    ap.add_argument("--reset", action="store_true", help="delete state.db first")
    ap.add_argument("--no-open", action="store_true", help="with --once: don't open the run report PDF")
    args = ap.parse_args()
    if args.reset and config.DB_PATH.exists():
        config.DB_PATH.unlink()
    if args.days:
        config.HAZARD_LOOKBACK_DAYS = args.days
    print(f"Semiconductor early-warning agents | models: {config.FAST_MODEL} (triage), {config.DEEP_MODEL} (deep) | "
          f"LLM {'ON' if llm.enabled() else 'OFF - rule-based fallback'} | cycle every {config.CYCLE_SECONDS}s")
    n = 1
    while True:
        cycle(n)
        if args.once:
            pdf = write_run_report()
            print(f"\nRun report: {pdf.relative_to(config.ROOT)}")
            if sys.platform == "darwin" and not args.no_open:
                subprocess.run(["open", str(pdf)])
            break
        n += 1
        try:
            time.sleep(config.CYCLE_SECONDS)
        except KeyboardInterrupt:
            break
    print("Stopped. State kept in state.db; notifications in out/notifications/.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
