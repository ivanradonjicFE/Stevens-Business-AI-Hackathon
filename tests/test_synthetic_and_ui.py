"""Tests for the synthetic demo feed, historical comparison, and the
chart-bearing TUI: the board must populate itself and every chart must
render non-empty text on a standard terminal.
"""

from __future__ import annotations

import asyncio
import io

from rich.console import Console

from watchtower import config
from watchtower.agents.orchestrator import Orchestrator
from watchtower.data.analogs._schema import load_analogs
from watchtower.models import Severity, Signal
from watchtower.synthetic import (
    STORYLINES,
    comparison_rows,
    historical_impact_index,
    synthetic_signals,
)
from watchtower.report import forecast_lead_days, rank_forecasts
from watchtower.ui.widgets import (
    BarChart,
    ForecastPanel,
    PctBarChart,
    SeverityHistoryChart,
    StatStrip,
    bar_line,
    pct_bar_line,
    sparkline_text,
)


def _plain(renderable, width: int = 140) -> str:
    """Render any Rich renderable to plain text for assertions."""
    console = Console(
        width=width, record=True, file=io.StringIO(), force_terminal=False
    )
    console.print(renderable)
    return console.export_text()


# --- synthetic feed ---------------------------------------------------------


def test_synthetic_signals_are_deterministic_and_well_formed() -> None:
    a = synthetic_signals(seed=7)
    b = synthetic_signals(seed=7)
    assert [s.signal_id for s in a] == [s.signal_id for s in b]
    assert len(a) > 20
    assert a == sorted(a, key=lambda s: s.ts)
    for sig in a:
        assert sig.geo is not None
        assert sig.entities
        assert sig.kind_hint
        assert 0.0 <= sig.credibility <= 1.0


def test_synthetic_seed_changes_timestamps() -> None:
    assert [s.ts for s in synthetic_signals(seed=1)] != [
        s.ts for s in synthetic_signals(seed=2)
    ]


def test_synthetic_feed_drives_pipeline_to_alert() -> None:
    orch = Orchestrator(config.load_markets()["semicon"])
    for signal in synthetic_signals(seed=7):
        orch.process(signal)
    events = orch.correlator.active_events()
    assert events, "synthetic feed produced no clusters"
    top = max(events, key=lambda e: e.severity_score)
    assert top.severity >= Severity.ELEVATED
    assert top.exposure, "top synthetic event has no chokepoint exposure"
    assert top.analogs, "synthetic event matched no historical analog"
    assert set(top.briefs) == {"health", "wealth", "insurance"}


def test_every_storyline_is_anchored_to_a_real_node() -> None:
    node_ids = {n.node_id for n in config.load_chokepoints()}
    assert {s.node_id for s in STORYLINES} <= node_ids


# --- historical comparison --------------------------------------------------


def test_historical_impact_index_is_normalized_and_ranked() -> None:
    analogs = load_analogs()
    for analog in analogs:
        assert 0.0 <= historical_impact_index(analog) <= 1.0
    rows = comparison_rows(analogs, top_n=5)
    assert len(rows) == 5
    values = [v for _, v in rows]
    assert values == sorted(values, reverse=True)


# --- leading indicators -----------------------------------------------------


def _forecast(ts: float, text: str, geo, signal_id: str) -> Signal:
    return Signal(
        ts=ts,
        source_type="analyst",
        source_name="Open-Meteo",
        url="",
        text=text,
        geo=geo,
        entities=("Test Port",),
        kind_hint="extreme_weather",
        credibility=0.55,
        signal_id=signal_id,
    )


def test_forecast_panel_renders_lead_times_and_distance() -> None:
    panel = ForecastPanel("EARLY INDICATORS")
    panel.update_forecasts(
        [
            ("+2d", "forecast: winds up to 95 km/h at Test Port", "Open-Meteo", 74.0),
            ("+1d", "forecast: rain 90 mm at Test Port", "NWS", None),
        ],
        width=60,
    )
    text = _plain(panel.flat_content)
    assert "EARLY INDICATORS" in text
    assert "+2d" in text and "+1d" in text
    assert "74km" in text


def test_forecast_panel_empty_state_flags_idle_weather_lane() -> None:
    panel = ForecastPanel()
    panel.update_forecasts([])
    assert "no forecast signals" in _plain(panel.flat_content)


def test_rank_forecasts_prefers_the_nearest_then_the_soonest() -> None:
    observed = _forecast(1.0, "port closure observed", (24.0, 121.0), "o1")
    near = _forecast(
        5.0, "forecast: winds 95 km/h at Near (+1d)", (24.2, 121.1), "f1"
    )
    far = _forecast(
        2.0, "forecast: winds 95 km/h at Far (+3d)", (40.0, -75.0), "f2"
    )
    picked = rank_forecasts((24.0, 121.0), [far, observed, near], limit=12)
    assert [s.signal_id for s in picked] == ["f1", "f2"]
    assert forecast_lead_days(near) == 1
    assert forecast_lead_days(observed) is None


# --- chart primitives -------------------------------------------------------


def test_bar_and_pct_lines_render_filled_and_empty_bars() -> None:
    full = _plain(bar_line("suez_canal", 1.0, 1.0, width=10, label_width=12))
    empty = _plain(bar_line("none", 0.0, 1.0, width=10, label_width=12))
    assert "█" * 10 in full
    assert "█" not in empty
    assert "100%" in _plain(pct_bar_line("match", 1.0, width=10))
    assert "0%" in _plain(pct_bar_line("match", 0.0, width=10))


def test_sparkline_scales_and_handles_short_series() -> None:
    assert sparkline_text([]) == ""
    short = sparkline_text([0.1, 0.2, 0.3], width=10)
    assert len(short) == 10  # right-padded to the requested width
    assert short.startswith(" ")
    rising = sparkline_text([0.0, 1.0], width=8)
    assert rising[0] < rising[-1]  # block glyphs are ordered by value


# --- app boot ---------------------------------------------------------------


def test_app_boots_populates_board_and_charts() -> None:
    from watchtower.ui.app import WatchtowerApp

    async def run() -> None:
        app = WatchtowerApp(feed="synthetic", cadence_s=0.01)
        async with app.run_test(size=(140, 46)) as pilot:
            await pilot.pause()
            for _ in range(60):
                await asyncio.sleep(0.02)
                if app._signals_seen >= 30:
                    break
            assert app._signals_seen > 10
            assert app._cluster_by_id
            assert len(app._order) >= 1

            stats = _plain(
                app.query_one("#stat-strip", StatStrip).flat_content, width=140
            )
            assert "SIGNALS" in stats and "FEED synthetic" in stats

            exposure = _plain(
                app.query_one("#exposure", BarChart).flat_content, width=140
            )
            assert "EXPOSURE" in exposure

            trajectory = _plain(
                app.query_one("#trajectory", SeverityHistoryChart).flat_content
            )
            assert "SEVERITY TRAJECTORY" in trajectory
            assert "▁" in trajectory or "█" in trajectory  # sparklines drawn

            analogs = _plain(
                app.query_one("#analogs", PctBarChart).flat_content
            )
            assert "ANALOG MATCH" in analogs

            outlook = _plain(
                app.query_one("#outlook").render(), width=140
            )
            assert "PRECEDENT: THEN vs NOW" in outlook

    asyncio.run(run())


def test_live_feed_ingests_into_the_pipeline_the_board_shows(monkeypatch) -> None:
    """Regression: a live poll must not feed a stale orchestrator.

    Two bugs made the live board show an empty event list: the feed Select
    emitted ``Changed`` on mount and rebuilt the pipeline twice, and the live
    pump held the orchestrator across its (slow) network fetch, so a restart
    mid-poll left every signal in a pipeline the UI no longer displayed. The
    board must ingest into whatever ``_orch`` currently is, exactly once.
    """
    from watchtower.synthetic import synthetic_signals
    from watchtower.ui.app import WatchtowerApp

    batch = list(synthetic_signals(seed=7))[:60]
    monkeypatch.setattr(WatchtowerApp, "_live_batch", lambda self: list(batch))

    restarts: list[str] = []
    original = WatchtowerApp._restart_pipeline

    def counting_restart(self):
        restarts.append(self.feed)
        return original(self)

    monkeypatch.setattr(WatchtowerApp, "_restart_pipeline", counting_restart)

    async def run() -> None:
        app = WatchtowerApp(feed="live", cadence_s=0.01)
        async with app.run_test(size=(140, 46)) as pilot:
            await pilot.pause()
            for _ in range(400):
                await asyncio.sleep(0.02)
                if app._signals_seen >= len(batch):
                    break
            await asyncio.sleep(0.3)  # let one coalesced render land
            assert app._signals_seen == len(batch)
            assert app._orch is not None
            assert app._orch.correlator.clusters, "signals went to a stale pipeline"
            assert len(app._order) >= 1, "no events surfaced on the live board"

    asyncio.run(run())
    # the mount-time Select Changed events must be no-ops, not rebuilds
    assert restarts == ["live"]


def test_live_forecasts_reach_the_early_indicator_panel(monkeypatch) -> None:
    """End to end: live forecast signals show up in the forecast panel.

    The panel must be fed from the whole forecast lane (a per-node forecast
    rarely clusters on its own) and must state each model's lead time.
    """
    from watchtower.ui.app import WatchtowerApp

    batch = [
        _forecast(1_000.0, "port closed - hull breach", (24.0, 121.0), "o1"),
        _forecast(
            2_000.0,
            "forecast: sustained winds up to 95 km/h at Test Port (+3d)",
            (24.2, 121.1),
            "f1",
        ),
    ]
    monkeypatch.setattr(WatchtowerApp, "_live_batch", lambda self: list(batch))

    async def run() -> None:
        app = WatchtowerApp(feed="live", cadence_s=0.01)
        async with app.run_test(size=(140, 46)) as pilot:
            await pilot.pause()
            for _ in range(200):
                await asyncio.sleep(0.02)
                if app._signals_seen >= len(batch):
                    break
            await asyncio.sleep(0.3)  # let one coalesced render land
            assert "f1" in app._forecasts
            text = _plain(
                app.query_one("#forecasts", ForecastPanel).flat_content, width=140
            )
            assert "+3d" in text
            assert "Open-Meteo" in text
            assert "km" in text
            # the observation must not masquerade as a lead-time row
            assert "+0d" not in text

    asyncio.run(run())
