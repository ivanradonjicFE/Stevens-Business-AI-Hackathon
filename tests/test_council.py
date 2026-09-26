"""Tests for the AI agent council.

Three things must hold whether or not a model is reachable:

* the council always produces a read (its rule fallback), so the dashboard
  never has an empty AI panel;
* the *lexical relevance* in that read is deterministic and never
  model-generated, so a number in a brief is reproducible from the config;
* the model may widen the relevance filter but not narrow it - missing a
  material shock is the expensive error.

No test here needs an API key; the model path is exercised by monkeypatching
``llm``.
"""

from __future__ import annotations

import asyncio
import io
import time

from rich.console import Console

from watchtower import config
from watchtower.agents import llm
from watchtower.agents.council import (
    LEVELS,
    NOTIFY_MIN_LEVEL,
    RELEVANCE_GATE,
    Council,
    _filter,
    _rule_filter,
)
from watchtower.agents.orchestrator import Orchestrator
from watchtower.models import EventCluster, Severity, Signal
from watchtower.relevance import lexical_relevance
from watchtower.report import event_report
from watchtower.synthetic import synthetic_signals

# Text deliberately dense in this market's vocabulary (watchlist, node kinds
# and the per-kind canonical terms in ``relevance.NODE_VOCAB``).
MARKET_TEXT = "TSMC wafer fab foundry capacity cut as chip supply tightens"
OFF_TOPIC_TEXT = "Local football derby ends in a draw after extra time"


def _market():
    return config.load_markets()["semicon"]


def _signal(
    text: str = MARKET_TEXT,
    credibility: float = 0.4,
    entities: tuple[str, ...] = ("tsmc", "wafer", "foundry"),
) -> Signal:
    """One signal; ``entities`` is passed explicitly because the relevance
    document is text *plus* entities (see ``tests/test_relevance.py``)."""
    return Signal(
        ts=1.0,
        source_type="social",
        source_name="wire",
        url="https://example.test",
        text=text,
        entities=entities,
        kind_hint="infrastructure",
        credibility=credibility,
    )


def _off_topic() -> list[Signal]:
    """A real-world event with no chip vocabulary anywhere, entities included."""
    return [_signal(OFF_TOPIC_TEXT, credibility=0.3, entities=())]


def _cluster(
    *,
    severity: Severity = Severity.GUARDED,
    score: float = 0.25,
    signals: list[Signal] | None = None,
    exposure: list | None = None,
    event_id: str = "evt-1",
) -> EventCluster:
    """A hand-built cluster, so the filter is tested without the correlator."""
    return EventCluster(
        event_id=event_id,
        title="Wafer capacity",
        signals=list(signals) if signals is not None else [_signal()],
        severity=severity,
        severity_score=score,
        exposure=list(exposure or []),
    )


def _plain(renderable, width: int = 140) -> str:
    console = Console(
        width=width, record=True, file=io.StringIO(), force_terminal=False
    )
    console.print(renderable)
    return console.export_text()


# --- the filter ------------------------------------------------------------


def test_lexical_overlap_admits_an_event_with_no_node_in_range() -> None:
    market = _market()
    verdict = _rule_filter(_cluster(), market)
    assert verdict["relevant"] is True
    assert verdict["route"] == "lexical"
    assert verdict["lexical"] >= RELEVANCE_GATE
    assert {"fab", "wafer", "foundry"} & set(verdict["lexical_matched"])
    assert "lexical relevance" in verdict["reason"]


def test_exposure_route_outranks_lexical() -> None:
    market = _market()
    node = config.load_chokepoints()[0]
    verdict = _rule_filter(_cluster(exposure=[(node, 0.8)]), market)
    assert verdict["route"] == "exposure"
    assert node.name in verdict["reason"]
    # the auditable number is reported on every route, not just the lexical one
    assert verdict["lexical"] >= 0.0


def test_off_topic_low_severity_is_rejected() -> None:
    market = _market()
    verdict = _rule_filter(_cluster(signals=_off_topic()), market)
    assert verdict["relevant"] is False
    assert verdict["route"] == "none"
    assert verdict["lexical"] < RELEVANCE_GATE
    assert verdict["lexical_matched"] == []


def test_filter_keeps_the_rule_relevance_when_the_model_says_no(
    monkeypatch,
) -> None:
    """A model exclusion of a rule-kept event is overridden and recorded."""
    market = _market()
    cluster = _cluster()
    rule = _rule_filter(cluster, market)
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm,
        "ask_json",
        lambda *a, **k: {
            "relevant": False,
            "credibility": 0.95,
            "reason": "not material to the chip chain",
        },
    )
    verdict = _filter(cluster, market)
    assert verdict["relevant"] is True
    assert "overridden" in verdict
    assert verdict["rule"]["relevant"] is True
    # provenance is copied from the rule verdict, never from the model
    assert verdict["lexical"] == rule["lexical"]
    assert verdict["lexical_matched"] == rule["lexical_matched"]
    assert verdict["route"] == rule["route"]
    assert verdict["lexical"] == lexical_relevance(cluster, market)


def test_filter_lets_the_model_widen_scope(monkeypatch) -> None:
    market = _market()
    cluster = _cluster(signals=_off_topic())
    rule = _rule_filter(cluster, market)
    assert rule["relevant"] is False
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm,
        "ask_json",
        lambda *a, **k: {
            "relevant": True,
            "credibility": 0.7,
            "reason": "supplier is a chip-logistics operator",
        },
    )
    verdict = _filter(cluster, market)
    assert verdict["relevant"] is True
    assert verdict["rule"]["relevant"] is False
    assert verdict["lexical"] == rule["lexical"]  # still deterministic


# --- the council -----------------------------------------------------------


def test_fallback_produces_a_read_without_a_model() -> None:
    market = _market()
    cluster = _cluster()
    council = Council(market)
    assert council._pool is None  # no model -> inline, deterministic

    report = council.consider(cluster)
    assert report is not None
    assert report.level in LEVELS
    assert report.model_used is False
    assert report.lexical == lexical_relevance(cluster, market)
    assert report.relevance_route == "lexical"
    assert report.relevance_reason
    assert report.trigger == "new event"
    assert "rule-based" in report.critique.lower()
    assert council.revision == 1
    assert "AI off" in council.status_line()
    assert "1 briefs" in council.status_line()


def test_status_line_reports_failed_calls_when_none_succeeded(
    monkeypatch,
) -> None:
    """A dead key or empty balance must not look like the model was never asked."""
    council = Council(_market())
    monkeypatch.setattr(llm, "status", lambda: "AI on: gpt-4o-mini")
    before = dict(llm.usage)
    llm.usage.update(calls=0, failures=6, prompt_tokens=0, completion_tokens=0)
    try:
        line = council.status_line()
    finally:
        llm.usage.update(before)
    assert "6 failed calls" in line
    assert "1 briefs" not in line  # no briefs were produced
    assert line.endswith("0 briefs")


def test_only_new_or_escalating_events_are_analysed() -> None:
    council = Council(_market())
    cluster = _cluster()
    assert council.consider(cluster) is not None
    assert council.revision == 1
    # same tier -> nothing new to say
    assert council.consider(cluster) is None
    assert council.revision == 1
    # escalation -> analysed again
    cluster.severity = Severity.HIGH
    assert council.consider(cluster) is not None
    assert council.revision == 2
    assert council.report_for(cluster.event_id).level == "WARNING"


def test_below_the_guarded_band_and_disabled_councils_do_nothing() -> None:
    council = Council(_market())
    assert council.consider(_cluster(severity=Severity.LOW, score=0.1)) is None
    assert council.reports == {}
    assert council.notifications() == []
    assert Council(_market(), enabled=False).consider(_cluster()) is None
    council.close()  # idempotent on the inline path
    council.close()
    assert council._pool is None


def test_notification_tier_is_watch_and_above() -> None:
    council = Council(_market())
    cluster = _cluster()
    advisory = council.consider(cluster)
    assert advisory is not None
    assert advisory.level == "ADVISORY"
    assert advisory.notifiable is False
    cluster.severity = Severity.HIGH
    warning = council.consider(cluster)
    assert warning is not None and warning.level == "WARNING"
    assert warning.notifiable is True
    notifications = council.notifications()
    assert notifications == [warning]
    assert all(r.level != "ADVISORY" for r in notifications)
    assert council.tally() == "1 WARNING"
    assert LEVELS.index(NOTIFY_MIN_LEVEL) == 1


def test_pool_path_dispatches_and_lands_the_brief(monkeypatch) -> None:
    """With a model reachable, work is dispatched and the brief lands later.

    The dashboard keys its repaint off ``revision``, so this verifies the
    off-thread finish path rather than the inline one the other tests use.
    """
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)  # rules only
    council = Council(_market(), max_workers=1)
    assert council._pool is not None
    try:
        cluster = _cluster(severity=Severity.ELEVATED)
        assert council.consider(cluster) is None  # dispatched, not inline
        deadline = time.time() + 5
        while time.time() < deadline and not council.reports:
            time.sleep(0.01)
        assert council.revision == 1
        assert council._inflight == set()
        report = council.report_for("evt-1")
        assert report is not None and report.level in LEVELS
    finally:
        council.close()
    assert council._pool is None


def test_model_path_only_spends_tokens_on_serious_events(monkeypatch) -> None:
    """Groq's free tier is ~8k tokens/minute, so quiet events are passed over."""
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    council = Council(_market(), min_interval_s=0.0)
    try:
        assert council.consider(_cluster(severity=Severity.GUARDED)) is None
        assert council._skipped == 1
        assert council.reports == {}  # nothing was spent
        assert council._inflight == set()
        assert "1 rationed" in council.status_line()
    finally:
        council.close()


def test_the_rule_path_is_never_rationed() -> None:
    """With no model the fallback is free, so every band still gets a read."""
    council = Council(_market())  # no key -> inline, no pool
    assert council._pool is None
    assert council.consider(_cluster(severity=Severity.GUARDED)) is not None
    assert council._skipped == 0
    assert "rationed" not in council.status_line()


def test_analyse_now_bypasses_rationing_for_the_reported_event(monkeypatch) -> None:
    """A one-shot report must brief *its* event, not the first one to arrive."""
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **k: None)
    # rationing that would refuse every background analysis
    council = Council(_market(), max_concurrent=0, min_interval_s=3600.0)
    try:
        assert council.consider(_cluster(severity=Severity.ELEVATED)) is None
        assert council._skipped == 1
        assert council.reports == {}
        # ...but the event the report names still gets a read
        report = council.analyse_now(_cluster(severity=Severity.ELEVATED))
        assert report is not None and report.level in LEVELS
        # and asking twice does not pay twice
        assert council.analyse_now(_cluster(severity=Severity.ELEVATED)) is report
        assert council.revision == 1
    finally:
        council.close()


def test_analyse_now_respects_the_base_band_and_the_disabled_flag() -> None:
    council = Council(_market())
    # below GUARDED is not worth spending a call on, even for a report
    assert council.analyse_now(_cluster(severity=Severity.LOW)) is None
    council.enabled = False
    assert council.analyse_now(_cluster(severity=Severity.ELEVATED)) is None


def test_model_path_rationing_predicate() -> None:
    """The concurrency cap and the start interval, tested without a race."""
    council = Council(_market(), max_concurrent=2, min_interval_s=30.0)
    try:
        with council._lock:
            assert council._may_start_model_locked()  # the first is always allowed
            council._last_model_start = time.monotonic()
            assert not council._may_start_model_locked()  # inside the interval
            council._last_model_start = time.monotonic() - 31.0
            assert council._may_start_model_locked()
            council._inflight.update({"a", "b"})
            assert not council._may_start_model_locked()  # at the cap
    finally:
        council.close()


def test_event_report_carries_the_council_read() -> None:
    cluster = _cluster()
    report = Council(_market()).consider(cluster)
    page = event_report(cluster, council_report=report)
    assert "## AI council read" in page
    assert "deterministic, computed from the market config" in page
    assert f"{report.lexical:.2f} of 1" in page
    assert "rule fallback (no model reachable)" in page
    assert report.level in page
    # a missing brief is stated rather than silently dropped
    assert "No council brief" in event_report(cluster)


# --- orchestrator wiring ---------------------------------------------------


def test_orchestrator_can_disable_the_council() -> None:
    orch = Orchestrator(_market(), enable_council=False)
    assert orch.council is None
    orch.process(next(iter(synthetic_signals(seed=7))))
    assert orch.council_report("anything") is None
    assert orch.council_notifications() == []


def test_orchestrator_runs_the_council_over_the_feed() -> None:
    orch = Orchestrator(_market())
    assert orch.council is not None
    for signal in synthetic_signals(seed=7):
        orch.process(signal)

    reports = orch.council.reports
    assert reports, "council produced no briefs from the synthetic feed"
    for report in reports.values():
        assert report.level in LEVELS
        assert report.model_used is False  # no key in CI
        assert report.lexical >= 0.0
        assert report.relevance_route in {"exposure", "severity", "lexical", "none"}
        # the rule fallback always explains itself
        assert report.relevance_reason
        assert report.critique
    for report in orch.council_notifications():
        assert report.notifiable

    # the first band crossings coincide with a council level upgrade, so
    # those alerts carry the tier that produced them
    assert orch.alerts
    assert any("council" in alert.headline for alert in orch.alerts)


def test_orchestrator_council_reads_survive_a_model_failure(monkeypatch) -> None:
    """A model that answers only the filter still leaves a usable report."""
    monkeypatch.setattr(llm, "available", lambda: True)

    def fake_ask(system, user, schema, *, name="result", **kwargs):
        if name == "relevance":
            return {"relevant": True, "credibility": 0.8, "reason": "model verdict"}
        return None  # every later stage falls back (timeout, bad key, rate limit)

    monkeypatch.setattr(llm, "ask_json", fake_ask)
    orch = Orchestrator(_market())
    orch.council._pool = None  # keep the run inline/deterministic
    for signal in synthetic_signals(seed=7):
        orch.process(signal)

    reports = list(orch.council.reports.values())
    assert reports
    assert any(report.model_used for report in reports)
    # the stages the model failed still produced grounded text
    assert all(r.research and r.market and r.critique for r in reports)
    assert all(r.lexical >= 0.0 for r in reports)


# --- TUI surfacing ---------------------------------------------------------


def test_tui_shows_the_council_status_and_read() -> None:
    from textual.widgets import ListView

    from watchtower.ui.app import WatchtowerApp

    async def run() -> None:
        app = WatchtowerApp(feed="synthetic", cadence_s=0.01)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            # briefs are written inline during the feed pump, so also wait for
            # a coalesced render frame before reading widget state
            for _ in range(150):
                await asyncio.sleep(0.02)
                council = app._orch.council if app._orch else None
                if council is not None and council.reports and app._row_sig:
                    break
            await pilot.pause()
            council = app._orch.council
            assert council is not None and council.reports

            stats = _plain(
                app.query_one("#stat-strip").flat_content, width=140
            )
            assert "AI COUNCIL" in stats
            assert "AI off" in stats
            assert "briefs" in stats

            # every event row leads its meta with the council tier
            metas = [sig[2] for sig in app._row_sig.values()]
            assert metas
            assert all(meta.startswith("ai ") for meta in metas)
            assert any(
                meta.split()[1] in LEVELS or meta.startswith("ai pending")
                for meta in metas
            )

            outlook = _plain(app.query_one("#outlook").render(), width=140)
            assert "AI COUNCIL" in outlook
            # the deterministic lexical number and its admitting route
            assert "lex " in outlook and "rules" in outlook

            # the outlook panel is repainted when a brief lands
            assert council.revision >= 1
            assert app.query_one("#events", ListView).children

    asyncio.run(run())


def test_dashboard_closes_the_council_pool_on_shutdown(monkeypatch) -> None:
    """A live council's pool is joined at exit, so it must be closed on quit."""
    from watchtower.agents import council as council_module
    from watchtower.ui.app import WatchtowerApp

    closed: list[object] = []
    monkeypatch.setattr(
        council_module.Council, "close", lambda self: closed.append(self)
    )

    async def run() -> None:
        app = WatchtowerApp(feed="synthetic", cadence_s=0.01)
        async with app.run_test(size=(140, 46)) as pilot:
            await pilot.pause()
            await asyncio.sleep(0.1)

    asyncio.run(run())
    assert closed, "the council pool was left open when the dashboard shut down"
