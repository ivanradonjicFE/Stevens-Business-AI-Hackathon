"""Scenario replay tests: every curated dataset must (a) parse through
the loader, (b) produce at least one cluster at ELEVATED or higher with
a nonzero analog match, and (c) never cite an excluded (future) analog.

Each scenario pins its own expected chokepoint exposure so a dataset
drifting away from the real geography fails loudly.
"""

from __future__ import annotations

import pytest

from watchtower import config
from watchtower.agents.orchestrator import Orchestrator
from watchtower.config import REPLAY_DIR, SCENARIO_EXCLUSIONS
from watchtower.models import Severity
from watchtower.sources import replay_signals

# scenario -> (market, expected top-exposure node_id, min severity)
# Weather-scoped demo set: hazard-driven events only (storm, drought,
# seismic adjacent weather). Conflict/labor/sabotage scenarios removed.
SCENARIOS = {
    "suez_2021": ("semicon", "suez_canal", Severity.ELEVATED),
    "panama_drought_2023": ("semicon", "panama_canal", Severity.ELEVATED),
    "turkey_syria_earthquake_2023": ("semicon", "ceyhan_terminal", Severity.ELEVATED),
}


def _run(scenario: str, market: str) -> Orchestrator:
    markets = config.load_markets()
    orch = Orchestrator(
        markets[market],
        exclude_analogs=SCENARIO_EXCLUSIONS.get(scenario, frozenset()),
    )
    for signal in replay_signals(REPLAY_DIR / f"{scenario}.jsonl"):
        orch.process(signal)
    return orch


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_scenario_escalates_and_exposes(scenario: str) -> None:
    market, expected_node, min_band = SCENARIOS[scenario]
    orch = _run(scenario, market)
    main = max(
        orch.correlator.active_events(),
        key=lambda e: e.severity_score,
    )
    assert main.severity >= min_band, (
        f"{scenario}: top event only reached {main.severity.name} "
        f"({main.severity_score:.2f})"
    )
    assert main.exposure, f"{scenario}: no chokepoint exposure"
    exposure_ids = {node.node_id for node, _ in main.exposure}
    assert (
        expected_node in exposure_ids
    ), f"{scenario}: expected {expected_node} in exposure, got {exposure_ids}"
    assert any(score > 0 for _, score in main.analogs)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_scenario_no_lookahead(scenario: str) -> None:
    market, _, _ = SCENARIOS[scenario]
    excluded = SCENARIO_EXCLUSIONS.get(scenario, frozenset())
    orch = _run(scenario, market)
    for event in orch.correlator.active_events():
        for analog, _ in event.analogs:
            assert analog.case_id not in excluded
