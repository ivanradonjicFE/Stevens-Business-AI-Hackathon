"""End-to-end pipeline test: the Suez replay must escalate to SEVERE
and never cite its own case file as an analog."""

from watchtower import config
from watchtower.agents.orchestrator import Orchestrator
from watchtower.config import REPLAY_DIR, SCENARIO_EXCLUSIONS
from watchtower.models import Severity
from watchtower.sources import replay_signals


def _run(scenario: str = "suez_2021") -> Orchestrator:
    markets = config.load_markets()
    orch = Orchestrator(
        markets["semicon"],
        exclude_analogs=SCENARIO_EXCLUSIONS.get(scenario, frozenset()),
    )
    for signal in replay_signals(REPLAY_DIR / f"{scenario}.jsonl"):
        orch.process(signal)
    return orch


def test_suez_replay_escalates_to_severe() -> None:
    orch = _run()
    bands = [a.severity for a in orch.alerts]
    assert Severity.SEVERE in bands
    assert bands == sorted(bands)  # upgrades only, monotone


def test_no_lookahead_in_analogs() -> None:
    orch = _run()
    for event in orch.correlator.active_events():
        for analog, _ in event.analogs:
            assert analog.case_id != "suez_2021"


def test_suez_event_exposes_suez_node() -> None:
    orch = _run()
    suez = max(
        orch.correlator.active_events(),
        key=lambda e: e.severity_score,
    )
    assert suez.exposure
    assert suez.exposure[0][0].node_id == "suez_canal"


def test_briefs_cover_three_lenses() -> None:
    orch = _run()
    suez = max(
        orch.correlator.active_events(),
        key=lambda e: e.severity_score,
    )
    assert set(suez.briefs) == {"health", "wealth", "insurance"}
