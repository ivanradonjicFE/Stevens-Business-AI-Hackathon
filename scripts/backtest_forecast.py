"""Backtest the early indicators on an archived scenario.

Answers the Phase-4 question: *if the report had been written the day before
the event, what would the weather models have told us, and how far ahead?*

    uv run python scripts/backtest_forecast.py suez_2021
    uv run python scripts/backtest_forecast.py panama_drought_2023 --previous

The forecast lane is replayed from Open-Meteo's archived runs, which are
point-in-time honest: the archive holds the forecast that was actually issued
for that date, and ``--previous`` goes further, rebuilding each day from the
run that many days ahead of it (``*_previous_dayN``) so a signal's ``(+Nd)``
lead time is the model's real horizon.

The hazard and river lanes are deliberately absent: GloFAS, NHC and NWS are
live-only feeds with no keyless history, so they cannot be replayed. That is a
coverage fact, not a bug, and it is printed with the result.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from watchtower import sources
from watchtower.config import (
    REPLAY_DIR,
    SCENARIO_EXCLUSIONS,
    load_chokepoints,
    load_markets,
    load_weather,
)
from watchtower.report import early_indicator_rows, is_forecast

ARCHIVED_DIR = REPLAY_DIR.parent / "replay_archived"


def _replay_path(scenario: str):
    """Prefer the newer replay set, fall back to the archived one."""
    for base in (REPLAY_DIR, ARCHIVED_DIR):
        candidate = base / f"{scenario}.jsonl"
        if candidate.exists():
            return candidate
    raise SystemExit(f"no replay file for scenario {scenario!r}")


def main() -> None:
    """Reconstruct the forecast view one day before an archived event."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", default="suez_2021")
    parser.add_argument("--market", default="semicon")
    parser.add_argument("--lead-days", type=int, default=3)
    parser.add_argument(
        "--at",
        default="",
        help="information cutoff (default: the day before the first observed signal)",
    )
    parser.add_argument(
        "--previous",
        action="store_true",
        help="also replay via the previous-runs API (exact per-run lead times)",
    )
    args = parser.parse_args()

    path = _replay_path(args.scenario)
    observed = [s for s in sources.replay_signals(path) if not is_forecast(s)]
    if not observed:
        raise SystemExit(f"{path.name} has no observed signals")
    first = min(observed, key=lambda s: s.ts)
    first_day = datetime.fromtimestamp(first.ts, UTC).date()
    cutoff = args.at or (first_day - timedelta(days=1)).isoformat()

    market = load_markets()[args.market]
    nodes = [n for n in load_chokepoints() if n.kind in market.node_kinds]
    exclude = SCENARIO_EXCLUSIONS.get(args.scenario, frozenset())
    spec = load_weather()

    print(f"=== scenario: {args.scenario} | market: {market.label} ===")
    print(f"observed signals: {len(observed)} | first event: {first_day} ({first.source_name})")
    print(f"information cutoff: {cutoff} | nodes watched: {len(nodes)}")
    if exclude:
        print(f"analogs excluded: {', '.join(sorted(exclude))}")

    archived = list(
        sources.openmeteo_forecast_signals(nodes, cutoff, spec=spec)
    )
    print(f"\n--- archived forecast lane: {len(archived)} early indicator(s) ---")
    for row in early_indicator_rows(archived):
        print(row)
    if not archived:
        print("(no threshold crossings in the window)")

    if args.previous:
        previous = list(
            sources.openmeteo_previous_run_signals(
                nodes, cutoff, spec=spec, lead_days=args.lead_days
            )
        )
        print(
            f"\n--- exact lead-time replay (previous runs, "
            f"{args.lead_days}d): {len(previous)} indicator(s) ---"
        )
        for row in early_indicator_rows(previous):
            print(row)

    print(
        "\nnote: GloFAS river low-water, NHC cyclones and NWS alerts are "
        "live-only feeds and\ncannot be replayed historically; only the "
        "forecast lane is backtested here."
    )


if __name__ == "__main__":
    main()
