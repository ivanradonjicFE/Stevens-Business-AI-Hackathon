"""Headless replay: run a scenario file through the whole pipeline.

Usage: uv run python scripts/replay.py [scenario] [--market semicon]
Prints every alert upgrade and a final per-event assessment.
"""

from __future__ import annotations

import argparse
import logging

from watchtower.agents.orchestrator import Orchestrator
from watchtower.config import REPLAY_DIR, SCENARIO_EXCLUSIONS, load_markets
from watchtower.sources import replay_signals


def main() -> None:
    """Run a replay scenario and print the resulting assessments."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", default="suez_2021")
    parser.add_argument("--market", default="semicon")
    args = parser.parse_args()

    market = load_markets()[args.market]
    exclude = SCENARIO_EXCLUSIONS.get(args.scenario, frozenset())
    orch = Orchestrator(market, exclude_analogs=exclude)

    path = REPLAY_DIR / f"{args.scenario}.jsonl"
    for signal in replay_signals(path):
        orch.process(signal)

    print(f"\n=== scenario: {args.scenario} | market: {market.label} ===")
    print(f"alerts issued: {len(orch.alerts)}")
    for alert in orch.alerts:
        print(f"  [{alert.severity.name}] {alert.headline}")

    for event in orch.correlator.active_events():
        print(f"\n--- {event.event_id}: {event.title} ---")
        print(
            f"  severity {event.severity.name} "
            f"({event.severity_score:.2f}) | signals {len(event.signals)}"
        )
        for c in event.components:
            print(f"    {c.name:<14} {c.value:.2f} x {c.weight:.2f}  {c.rationale}")
        for node, score in event.exposure[:3]:
            print(f"    exposed: {node.name} ({score:.2f})")
        for analog, score in event.analogs:
            print(f"    analog:  {analog.name} ({score:.2f})")


if __name__ == "__main__":
    main()
