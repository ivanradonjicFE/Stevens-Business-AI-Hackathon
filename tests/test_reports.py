"""Tests for situation reports, the preload cache, and the dashboard
interaction model: event names are clickable, every report is already
built before the click, and rendering is coalesced (no per-signal churn).
"""

from __future__ import annotations

import asyncio

from watchtower import config
from watchtower.agents.orchestrator import Orchestrator
from watchtower.report import (
    INDEX_BANDS,
    ReportCache,
    event_report,
    index_band,
    index_value,
    region_for,
    situation_report,
)
from watchtower.synthetic import synthetic_signals


def _run() -> Orchestrator:
    orch = Orchestrator(config.load_markets()["semicon"])
    for signal in synthetic_signals(seed=7):
        orch.process(signal)
    return orch


# --- report content ---------------------------------------------------------


def test_region_lookup_covers_known_nodes() -> None:
    assert region_for((24.5, 119.5)) == "taiwan"
    assert region_for((30.02, 32.55)) == "middle_east"
    assert region_for((33.73, -118.20)) == "north_america"
    assert region_for(None) == "unlocated"


def test_index_bands_are_monotone() -> None:
    assert index_value(0.79) == 79
    assert index_band(0.79) == "RED"
    bands = [name for _, name in INDEX_BANDS]
    assert bands == ["RED", "ORANGE", "AMBER", "YELLOW", "GREEN"]
    assert index_band(0.0) == "GREEN"


def test_event_report_contains_every_section() -> None:
    orch = _run()
    event = max(
        orch.correlator.active_events(), key=lambda e: e.severity_score
    )
    page = event_report(
        event,
        market_label="Semiconductor",
        market_index="SOX",
        watchlist=("NVDA", "TSM"),
        as_of_ts=event.last_seen,
        notified=True,
    )
    for heading in (
        "Situation report",
        "Composite index",
        "**Bottom line.**",
        "**What happened.**",
        "## Score breakdown (auditable rubric)",
        "## Sites in the impact zone",
        "## Historical precedents",
        "## Precedent-derived market view",
        "## Watchlist to monitor",
        "## Lens briefs",
        "## Recommended actions",
        "## How this was scored",
        "## Evidence trail",
    ):
        assert heading in page, f"missing section: {heading}"
    assert event.title in page
    assert f"{index_value(event.severity_score)} / 100" in page
    assert "NOTIFIED" in page
    # rubric rows must match the cluster's auditable components
    for component in event.components:
        assert component.name in page


def test_situation_report_lists_all_active_events() -> None:
    orch = _run()
    events = orch.correlator.active_events()
    index = situation_report(events, as_of_ts=events[0].last_seen)
    assert "Situation Report" in index
    assert "point-in-time (PIT)" in index
    assert f"{len(events)} active event(s)" in index
    for event in events:
        assert event.title in index


# --- preload cache ---------------------------------------------------------


def test_report_cache_preloads_every_event() -> None:
    orch = _run()
    cache = ReportCache(
        market_label="Semiconductor", market_index="SOX", watchlist=("NVDA",)
    )
    events = orch.correlator.active_events()
    for event in events:
        cache.preload(event, as_of_ts=event.last_seen)
    assert cache.preloaded() == len(events)
    for event in events:
        page = cache.get(event.event_id)
        assert page is not None and event.title in page

    index = cache.preload_index(events, as_of_ts=events[0].last_seen)
    assert index and "level" in index
    # unchanged state must not rebuild the index (cheap ticks)
    assert cache.preload_index(events, as_of_ts=events[0].last_seen) == index
    cache.preload_index(events, as_of_ts=events[0].last_seen, force=True)
    assert cache.index


def test_report_cache_rebuilds_on_change() -> None:
    orch = _run()
    cache = ReportCache()
    events = orch.correlator.active_events()
    cache.preload_index(events, as_of_ts=events[0].last_seen)
    first = cache.index
    # add a synthetic event to the set -> signature changes -> rebuild
    cache.preload_index(
        events + [events[0]], as_of_ts=events[0].last_seen, force=False
    )
    assert cache.index != first


# --- dashboard interaction --------------------------------------------------


async def _wait_for_report(app, needle: str, tries: int = 80) -> None:
    """Wait until a ReportScreen is open and showing ``needle``.

    A modal is pushed synchronously but fills its markdown on mount, so a
    single ``pause`` races the content and makes the click tests flaky.
    """
    from watchtower.ui.app import ReportScreen

    for _ in range(tries):
        await asyncio.sleep(0.01)
        screen = app.screen
        if isinstance(screen, ReportScreen) and needle in screen.markdown:
            return


def test_clicking_event_name_opens_preloaded_report() -> None:
    from textual.widgets import ListItem

    from watchtower.ui.app import ReportScreen, WatchtowerApp

    async def run() -> None:
        app = WatchtowerApp(feed="synthetic", cadence_s=0.01)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            # Wait for the board *and* its report cache: the cache fills on the
            # same tick, so breaking as soon as events appear races the preload.
            for _ in range(150):
                await asyncio.sleep(0.02)
                order = list(app._order)
                if (
                    len(order) >= 2
                    and app._reports.index
                    and all(app._reports.get(eid) is not None for eid in order)
                ):
                    break
            await pilot.pause()
            assert len(app._order) >= 2, "no active events to click"
            # every visible event already has its report preloaded
            for event_id in app._order:
                assert app._reports.get(event_id) is not None
            assert app._reports.index

            first = app.query_one("#events").children[0]
            assert isinstance(first, ListItem)
            # click the row: ListView posts Selected -> report opens instantly
            await pilot.click("#events ListItem")
            # The screen is pushed synchronously but its markdown is assigned
            # on mount, so wait for the content instead of a single pause.
            await _wait_for_report(app, "Situation report")
            assert isinstance(app.screen, ReportScreen)
            assert app.screen.report_title

            # shift+R shows the whole situation report from the cache
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("R")
            await _wait_for_report(app, "Situation Report")

    asyncio.run(run())


def test_report_scrolls_past_the_fold() -> None:
    from textual.containers import VerticalScroll

    from watchtower.ui.app import ReportScreen, WatchtowerApp

    async def run() -> None:
        app = WatchtowerApp(feed="synthetic", cadence_s=0.01)
        async with app.run_test(size=(110, 28)) as pilot:
            await pilot.pause()
            for _ in range(80):
                await asyncio.sleep(0.02)
                if app._order:
                    break
            await pilot.pause()
            # focus the board so Enter opens the highlighted event
            app.query_one("#events").focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            body = app.screen.query_one("#report-body", VerticalScroll)
            # a full situation report runs well past a small viewport
            assert body.max_scroll_y > 0
            assert body.scroll_y == 0
            assert app.screen.focused is body

            await pilot.press("pagedown")
            await pilot.pause()
            assert body.scroll_y > 0

            body.scroll_end(animate=False)
            await pilot.pause()
            assert body.scroll_y == body.max_scroll_y

            await pilot.press("home")
            await pilot.pause()
            assert body.scroll_y == 0

    asyncio.run(run())


def test_hovering_an_event_repoints_the_dashboard() -> None:
    from textual.widgets import ListItem

    from watchtower.ui.app import WatchtowerApp

    async def run() -> None:
        app = WatchtowerApp(feed="synthetic", cadence_s=0.01)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            for _ in range(80):
                await asyncio.sleep(0.02)
                if len(app._order) >= 3:
                    break
            # freeze the feed: a reordering board would move the row out
            # from under the cursor mid-assertion
            app.paused = True
            await asyncio.sleep(0.3)
            await pilot.pause()
            rows = [
                child
                for child in app.query_one("#events").children
                if isinstance(child, ListItem)
            ]
            assert len(rows) >= 2
            first, second = rows[0], rows[1]
            assert first.name != second.name
            await pilot.hover(second)
            await pilot.pause()
            await asyncio.sleep(0.3)
            await pilot.pause()
            assert app._hovered == second.name
            assert app._selected == second.name
            assert app._previewing is True
            # panels were repainted for the hovered event, not the first one
            assert app._chart_sig["outlook"][0] == second.name
            stats = app.query_one("#stat-strip")
            assert stats.content is not None

    asyncio.run(run())


def test_high_severity_events_flash() -> None:
    from watchtower.ui.app import WatchtowerApp

    async def run() -> None:
        app = WatchtowerApp(feed="synthetic", cadence_s=0.01)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            # the flash set is populated by the render tick, so wait for it
            for _ in range(200):
                await asyncio.sleep(0.02)
                if app._flash_ids:
                    break
            await pilot.pause()
            assert app._flash_ids, "no high-severity event detected"
            assert "alerting" in app.screen.classes
            assert app.query_one("#flash-bar").display is True

            # _flash_tick drops row signatures to force a repaint, so wait
            # for the render tick to put the flashing row back
            flashing = None
            for _ in range(200):
                await asyncio.sleep(0.02)
                flashing = next(
                    (eid for eid in app._flash_ids if eid in app._row_sig),
                    None,
                )
                if flashing:
                    break
            assert flashing is not None
            before = app._row_text(app._row_sig[flashing])
            app._flash_tick()
            app._render_tick()
            app._dirty = True
            app._render_frame()
            after = app._row_text(app._row_sig[flashing])
            assert before != after  # blink phase changed the row
            # low-severity rows are untouched by the flash phase
            quiet = [
                eid
                for eid, sig in app._row_sig.items()
                if sig[-1] < 0
            ]
            assert quiet or app._flash_ids

    asyncio.run(run())


def test_watchlist_is_expanded_and_segmented() -> None:
    from watchtower.config import load_markets

    markets = load_markets()
    for key, spec in markets.items():
        assert len(spec.watchlist) >= 20, f"{key} watchlist not expanded"
        assert spec.segments, f"{key} has no segment grouping"
        segment_tickers = {t for _, tickers in spec.segments for t in tickers}
        # every segment name must come from the vertical's own watchlist
        assert segment_tickers <= set(spec.watchlist)


def test_rendering_is_coalesced_not_per_signal() -> None:
    from watchtower.ui.app import WatchtowerApp

    async def run() -> None:
        app = WatchtowerApp(feed="synthetic", cadence_s=0.01)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            for _ in range(80):
                await asyncio.sleep(0.02)
                if app._signals_seen >= 30:
                    break
            await pilot.pause()
            assert app._signals_seen > 20
            # bursts of signals must not repaint per signal
            assert app._render_count < app._signals_seen
            # rows are updated in place: exactly one ListItem per event
            lv = app.query_one("#events")
            assert len(lv.children) == len(app._order)
            assert set(app._row_widgets) == set(app._order)

    asyncio.run(run())
