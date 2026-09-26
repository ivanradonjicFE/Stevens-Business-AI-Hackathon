"""Watchtower TUI: centered, responsive supply-shock monitor with charts.

Feeds: ``synthetic`` (generated demo storylines, always populated) or any
curated replay file in ``data/replay``. The dashboard pre-fills instantly,
then streams at demo cadence.

Keys: space pause/resume, enter event audit, r restart, q quit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import textwrap
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView, Markdown, Select, Static

from watchtower.agents.orchestrator import Orchestrator
from watchtower.config import REPLAY_DIR, SCENARIO_EXCLUSIONS, load_markets
from watchtower.geo import haversine_km
from watchtower.models import EventCluster, Severity, Signal
from watchtower.report import (
    ReportCache,
    forecast_claim,
    forecast_lead_days,
    is_forecast,
    rank_forecasts,
)
from watchtower.sources import replay_signals
from watchtower.synthetic import synthetic_signals
from watchtower.ui.widgets import (
    SEVERITY_STYLE,
    BarChart,
    PctBarChart,
    FlashBar,
    ForecastPanel,
    SeverityHistoryChart,
    StatStrip,
    TimeFlowChart,
)

log = logging.getLogger(__name__)

DEFAULT_CADENCE_S = 0.9
PRELOAD_SIGNALS = 12  # processed instantly so the board is never empty
SYNTHETIC_FEED = "synthetic"
LIVE_FEED = "live"  # keyless news + weather + hazard feeds, polled
#: How often to re-poll the live sources, and how far back each poll looks.
#: GDELT wants 1 request/5s and the free weather tier is ~10k calls/day, so
#: polling gently matters more here than freshness to the second.
LIVE_POLL_S = 60.0
LIVE_LOOKBACK_H = 12
RENDER_INTERVAL_S = 0.15  # coalesced repaint interval (anti-flicker)
FLASH_INTERVAL_S = 1.1  # blink phase for HIGH/SEVERE events (slow pulse)
FLASH_BAND = Severity.HIGH  # events at or above this band flash
PRELOAD_PER_TICK = 25  # report pages rebuilt per frame (see _flush_preloads)
FORECAST_ROWS = 4  # leading-indicator rows shown in the forecast panel
_FORECAST_PREFIX = "forecast:"  # stripped from panel rows (the lane speaks for itself)
FORECAST_KEEP = 200  # cap on retained forecast signals (live polls re-read)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%MZ")


def _wrap(text: str, width: int, indent: str = "  ") -> list[str]:
    """Wrap prose to a panel width, keeping the indent on every line."""
    return textwrap.wrap(
        text.strip(),
        width=max(10, width),
        initial_indent=indent,
        subsequent_indent=indent,
    )


def _fit(text: str, width: int) -> str:
    """Hard-truncate a single plain line to a panel width."""
    return text if len(text) <= width else text[: max(1, width - 1)] + "\u2026"


def _wrap_capped(
    text: str, width: int, max_lines: int, indent: str = "  "
) -> list[str]:
    """Wrap prose, truncating to ``max_lines`` with an ellipsis.

    Panels have a fixed height, so long model prose has to be capped rather
    than allowed to push the rest of the panel out of view.
    """
    lines = _wrap(text, width, indent)
    if len(lines) <= max_lines:
        return lines
    tail = lines[max_lines - 1][: max(1, width - 1)]
    return [*lines[: max_lines - 1], tail + "\u2026"]


class ReportScreen(ModalScreen):
    """Scrollable situation report, rendered from the preloaded cache."""

    def __init__(self, title: str, markdown: str) -> None:
        super().__init__()
        self.report_title = title
        self.markdown = markdown

    def compose(self) -> ComposeResult:
        """Render the report document and its close hint.

        The body sits in a scrollable container: a full report is far
        taller than the screen, so the title and hint stay pinned while
        the document scrolls under them.
        """
        yield Vertical(
            Static(self.report_title, id="report-title"),
            VerticalScroll(Markdown(self.markdown, id="report-md"), id="report-body"),
            Static(
                "esc close  |  \u2191\u2193 / pgup / pgdn scroll  |  preloaded",
                id="report-hint",
            ),
            id="report",
        )

    def on_mount(self) -> None:
        """Focus the body so the keyboard scrolls the report."""
        self.query_one("#report-body", VerticalScroll).focus()

    def on_key(self, event: events.Key) -> None:
        """Escape closes the report."""
        if event.key == "escape":
            self.app.pop_screen()


class WatchtowerApp(App):
    """Main dashboard: centered KPI strip, event board, and live charts."""

    TITLE = "WATCHTOWER"
    SUB_TITLE = "supply-shock early warning"

    CSS = """
    Screen { background: $surface; }

    #title { width: 100%; text-align: center; content-align: center middle;
             text-style: bold; color: cyan; padding-top: 1; }
    #controls { height: 3; align: center middle; }
    #market-select { width: 24; }
    #feed-select { width: 34; margin-left: 1; }
    #hint { margin-left: 2; color: $text-muted; }
    #stat-strip { height: 4; width: 100%; content-align: center middle; }
    #flash-bar { height: 1; width: 100%; display: none; }
    Screen.alerting #flash-bar { display: block; }

    #main-grid { layout: horizontal; height: 1fr; }
    #main-grid .column { width: 1fr; height: 100%; }
    .panel { border: round $accent; padding: 0 1; height: 1fr; }
    #events { border: round $accent; height: 1fr; }
    #trajectory { height: 7; }
    #forecasts { height: 8; }
    #watchlist { height: 11; }
    #flow-strip { height: 7; layout: horizontal; }
    #flow-strip .panel { border: round $accent; padding: 0 1; height: 100%;
                         width: 1fr; }
    #ticker { height: 1; padding: 0 1; color: $text-muted; }
    #report { width: 94%; height: 94%; border: heavy $accent;
              background: $surface; padding: 1; }
    #report-title { text-style: bold; color: cyan; text-align: center;
                    width: 100%; }
    #report-body { height: 1fr; overflow-y: auto; }
    #report-md { height: auto; }
    #report-hint { color: $text-muted; text-align: right; }

    /* Clickable event names: hover affordance for mouse users. */
    #events ListItem:hover { background: $accent 25%; }
    #events ListItem:hover Label { text-style: underline; }

    /* Responsive: drop optional panels, then stack columns. */
    Screen.short #flow-strip { height: 5; }
    Screen.short #watchlist { display: none; }
    Screen.short #forecasts { height: 7; }
    Screen.stacked #main-grid { layout: vertical; }
    Screen.stacked #main-grid .column { width: 100%; height: auto; }
    Screen.stacked #events { height: 10; }
    Screen.stacked #exposure { height: 9; }
    Screen.stacked #analogs { height: 9; }
    Screen.stacked #forecasts { height: 8; }
    Screen.stacked #watchlist { height: 10; }
    """

    BINDINGS: ClassVar[list] = [
        ("space", "toggle_pause", "pause/resume"),
        ("enter", "report", "report"),
        ("R", "situation", "situation report"),
        ("r", "restart", "restart"),
        ("q", "quit", "quit"),
    ]

    def __init__(
        self,
        market_key: str = "semicon",
        feed: str = SYNTHETIC_FEED,
        cadence_s: float = DEFAULT_CADENCE_S,
        seed: int = 7,
        loop_feed: bool = True,
    ) -> None:
        super().__init__()
        self.markets = load_markets()
        self.market_key = market_key
        self.feed = feed
        self.cadence_s = cadence_s
        self.seed = seed
        self.paused = False
        self._signal_iter: Iterator[Signal] = iter(())
        self._orch: Orchestrator | None = None
        self._order: list[str] = []
        self._cluster_by_id: dict[str, EventCluster] = {}
        self._history: dict[str, list[float]] = {}
        self._preload_dirty: set[str] = set()
        self._selected: str | None = None
        self._signals_seen = 0
        self._sim_time: datetime | None = None
        self._feed_done = False
        self._reports = ReportCache()
        # Coalesced rendering (anti-flicker) + incremental widget state.
        self._dirty = False
        self._render_count = 0
        self._last_signal: Signal | None = None
        self._ticker_line = ""
        self._row_widgets: dict[str, ListItem] = {}
        self._row_sig: dict[str, tuple[str, str, str, float, int]] = {}
        self._chart_sig: dict[str, object] = {}
        self._signal_times: list[float] = []
        # Forecast signals have no event of their own (the correlator wants two
        # signals), so the panel draws from every one seen so far.
        self._forecasts: dict[str, Signal] = {}
        self._flow_buckets = 24
        self._last_ts: float | None = None
        self._cycle = 0
        self._hovered: str | None = None
        self._previewing = False
        self._flash_phase = 0
        self._flash_ids: frozenset[str] = frozenset()
        self._council_revision = 0
        self.loop_feed = loop_feed

    # --- feed ------------------------------------------------------------

    def _build_orch(self) -> Orchestrator:
        exclude = SCENARIO_EXCLUSIONS.get(self.feed, frozenset())
        return Orchestrator(self.markets[self.market_key], exclude_analogs=exclude)

    def _build_signals(self) -> Iterator[Signal]:
        if self.feed == SYNTHETIC_FEED:
            return iter(synthetic_signals(seed=self.seed))
        if self.feed == LIVE_FEED:
            # Signals come from the polling loop, not from an iterator.
            return iter(())
        return replay_signals(REPLAY_DIR / f"{self.feed}.jsonl")

    def _feed_options(self) -> list[tuple[str, str]]:
        options = [("Synthetic live feed (generated)", SYNTHETIC_FEED)]
        options.append(("Live feeds (news + weather + hazards)", LIVE_FEED))
        options += [
            (p.stem.replace("_", " "), p.stem)
            for p in sorted(REPLAY_DIR.glob("*.jsonl"))
        ]
        return options

    def _live_batch(self) -> list[Signal]:
        """One poll of every keyless live source (runs off the UI thread)."""
        from datetime import timedelta

        from watchtower.sources import (
            hazard_signals,
            news_signals,
            weather_signals,
        )

        now = datetime.now(UTC)
        since = now - timedelta(hours=LIVE_LOOKBACK_H)
        compact_now = now.strftime("%Y%m%d%H%M%S")
        compact_since = since.strftime("%Y%m%d%H%M%S")
        batch: list[Signal] = []
        for name, scout in (
            ("hazards", lambda: hazard_signals(
                since.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
            )),
            ("news", lambda: news_signals(
                compact_since, compact_now, market_key=self.market_key
            )),
            ("weather", lambda: weather_signals(
                end=compact_now, market_key=self.market_key
            )),
        ):
            try:
                batch.extend(scout())
            except Exception:  # noqa: BLE001 - one dead feed must not stop the poll
                log.warning("live feed %s failed", name, exc_info=True)
        return sorted(batch, key=lambda s: s.ts)

    async def _pump_live(self) -> None:
        """Poll the live sources and feed whatever is new into the pipeline.

        Unlike the replay feeds this never ends, so a live board keeps
        accepting events. Signals are de-duplicated by their content-derived
        id, because every poll re-reads a rolling window.
        """
        seen: set[str] = set()
        while True:
            while self.paused:
                await asyncio.sleep(0.1)
            orch = self._orch
            if orch is None:
                await asyncio.sleep(0.05)
                continue
            try:
                batch = await asyncio.to_thread(self._live_batch)
            except Exception:  # noqa: BLE001
                log.warning("live poll failed", exc_info=True)
                batch = []
            # A poll takes tens of seconds, and a market/feed switch or a
            # duplicate mount event can replace the pipeline while it is in
            # flight. Ingesting into the *old* orchestrator leaves the board
            # showing a fresh, empty one, so re-read and drop a stale batch.
            if self._orch is not orch:
                await asyncio.sleep(0.05)
                continue
            fresh = [s for s in batch if s.signal_id and s.signal_id not in seen]
            for signal in fresh:
                seen.add(signal.signal_id)
                self._ingest(signal, orch.process(signal))
                await asyncio.sleep(0.05)
            self._cycle = 0
            self._dirty = True
            await asyncio.sleep(LIVE_POLL_S)

    # --- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Centered header, KPI strip, three chart columns, ticker, footer."""
        market_options = [(spec.label, key) for key, spec in self.markets.items()]
        yield Static(
            "W A T C H T O W E R   |   supply-shock early warning", id="title"
        )
        yield Horizontal(
            Select(market_options, value=self.market_key, id="market-select"),
            Select(self._feed_options(), value=self.feed, id="feed-select"),
            Static(
                "hover an event to preview  |  click for its report  |  R full"
                " report  |  space pause",
                id="hint",
            ),
            id="controls",
        )
        yield FlashBar(id="flash-bar")
        yield StatStrip(id="stat-strip")
        yield Horizontal(
            Vertical(ListView(id="events"), classes="column"),
            Vertical(
                SeverityHistoryChart(id="trajectory", classes="panel"),
                BarChart("EXPOSURE - NEAREST NODES", id="exposure", classes="panel"),
                PctBarChart("ANALOG MATCH", id="analogs", classes="panel"),
                classes="column",
            ),
            Vertical(
                Static("", id="outlook", classes="panel"),
                ForecastPanel(
                    "EARLY INDICATORS - MODELS SAY",
                    id="forecasts",
                    classes="panel",
                ),
                Static("", id="watchlist", classes="panel"),
                classes="column",
            ),
            id="main-grid",
        )
        yield Horizontal(
            TimeFlowChart("SIGNAL FLOW", id="flow", classes="panel"),
            PctBarChart("LEVEL MIX", id="level-mix", classes="panel"),
            id="flow-strip",
        )
        yield Static("feed idle", id="ticker")
        yield Footer()

    def on_mount(self) -> None:
        """Start the feed plus the render and flash loops."""
        self.call_after_refresh(self._restart_pipeline)
        self.set_interval(RENDER_INTERVAL_S, self._render_tick)
        self.set_interval(FLASH_INTERVAL_S, self._flash_tick)
        self.run_worker(self._pump(), exclusive=True)

    # --- replay driver ---------------------------------------------------

    async def _pump(self) -> None:
        """Feed signals through the pipeline: instant prefill, then cadence.

        The synthetic feed keeps rolling (fresh seed, continuing timeline)
        so live events keep arriving and keep matching the historical
        library; curated replays stop when their file ends.
        """
        while self._orch is None:  # wait for the first layout pass
            await asyncio.sleep(0.05)
        if self.feed == LIVE_FEED:
            await self._pump_live()
            return
        while True:
            for index, signal in enumerate(self._signal_iter):
                while self.paused:
                    await asyncio.sleep(0.1)
                orch = self._orch
                if orch is None:
                    raise RuntimeError("pipeline not initialized")
                cluster = orch.process(signal)
                self._ingest(signal, cluster)
                await asyncio.sleep(
                    0.02 if index < PRELOAD_SIGNALS else self.cadence_s
                )
            if self.feed != SYNTHETIC_FEED or not self.loop_feed:
                break
            self._cycle += 1
            self._signal_iter = iter(
                synthetic_signals(
                    seed=self.seed + self._cycle,
                    start_ts=(self._last_ts or 0.0) + 6 * 3600,
                )
            )
            self._dirty = True
        self._feed_done = True
        self._dirty = True

    def _ingest(self, signal: Signal, cluster: EventCluster) -> None:
        """Fold one signal into app state; painting happens on the next tick.

        Nothing here touches the DOM: bursts of signals mark the board
        dirty and a single coalesced render runs on the interval, which
        is what keeps the terminal from flickering during fast feeds.
        """
        self._cluster_by_id[cluster.event_id] = cluster
        self._signals_seen += 1
        self._sim_time = datetime.fromtimestamp(signal.ts, UTC)
        self._last_signal = signal
        self._last_ts = signal.ts
        self._history.setdefault(cluster.event_id, []).append(cluster.severity_score)
        self._signal_times.append(signal.ts)
        if is_forecast(signal) and signal.signal_id:
            self._forecasts[signal.signal_id] = signal
            while len(self._forecasts) > FORECAST_KEEP:
                self._forecasts.pop(next(iter(self._forecasts)))
        # Preload is *marked*, not done: building a page here would also
        # rebuild the whole report index, which is what made a live burst crawl
        # at about a signal a second. The render tick coalesces it instead.
        self._preload_dirty.add(cluster.event_id)
        self._dirty = True

    def _flush_preloads(self) -> None:
        """Rebuild pages for events touched since the last frame, then the index.

        Coalesced exactly like the repaint is. ``PRELOAD_PER_TICK`` bounds the
        work per frame so a large live batch cannot stall the UI.
        """
        if not self._preload_dirty:
            return
        orch = self._orch
        as_of = self._sim_time.timestamp() if self._sim_time else None
        notified = (
            frozenset(a.event.event_id for a in orch.alerts)
            if orch is not None
            else frozenset()
        )
        for event_id in list(self._preload_dirty)[:PRELOAD_PER_TICK]:
            self._preload_dirty.discard(event_id)
            cluster = self._cluster_by_id.get(event_id)
            if cluster is not None:
                self._reports.preload(
                    cluster,
                    as_of_ts=as_of,
                    notified=event_id in notified,
                    council=self._council_report(event_id),
                )
        if orch is not None:
            self._reports.preload_index(
                orch.correlator.active_events(),
                as_of_ts=as_of,
                notified_ids=notified,
            )

    # --- render loop (coalesced: no per-signal repaint) -------------------

    def _render_tick(self) -> None:
        """Repaint at most once per interval, only when state changed."""
        # Council briefs land off-thread when a model is reachable, so a new
        # revision is a repaint trigger in its own right.
        orch = self._orch
        revision = orch.council.revision if orch and orch.council else 0
        if revision != self._council_revision:
            self._council_revision = revision
            self._dirty = True
        if self._preload_dirty:
            # keep draining a large batch over successive frames
            self._dirty = True
        if not self._dirty:
            return
        self._dirty = False
        self._render_count += 1
        try:
            self._flush_preloads()
            self._render_frame()
        except NoMatches:
            return

    def _render_frame(self) -> None:
        """Sync every panel with current state, skipping unchanged widgets."""
        orch = self._orch
        active = orch.correlator.active_events() if orch else []
        self._order = [c.event_id for c in active]
        self._update_flash_set(active)
        self._sync_event_rows(active)
        self._render_stats(active)
        self._render_flash_bar(active)
        self._render_ticker()
        self._render_flow_charts(active)
        if self._order:
            target = self._selected
            if target not in self._cluster_by_id:
                target = self._order[0]
            self._select_event(self._cluster_by_id[target])
        else:
            # No event to anchor the forecast lane against yet, but the panel
            # must still clear when a feed is switched or restarted.
            self._render_forecasts(None)

    # --- flashing high-severity events -----------------------------------

    def _update_flash_set(self, events: list[EventCluster]) -> None:
        """Track which events are severe enough to flash."""
        ids = frozenset(
            ev.event_id for ev in events if ev.severity >= FLASH_BAND
        )
        if ids != self._flash_ids:
            self._flash_ids = ids
            self.screen.set_class(bool(ids), "alerting")
            for event_id in ids:
                self._row_sig.pop(event_id, None)  # force a repaint

    def _flash_tick(self) -> None:
        """Flip the blink phase for flashing rows only."""
        if not self._flash_ids or len(self.screen_stack) > 1:
            return
        self._flash_phase += 1
        for event_id in self._flash_ids:
            self._row_sig.pop(event_id, None)
        self._dirty = True

    def _render_flash_bar(self, events: list[EventCluster]) -> None:
        """Headline banner for the most urgent flashing event."""
        flashing = [e for e in events if e.event_id in self._flash_ids]
        if not flashing:
            return
        top = max(flashing, key=lambda e: (e.severity, e.severity_score))
        n_src = len({s.source_type for s in top.signals})
        text = (
            f"{top.severity.name} ALERT  {top.title}  |  "
            f"{len(top.signals)} signals / {n_src} sources  |  "
            f"composite {top.severity_score:.2f}  |  enter for report"
        )
        # The banner is steady - it only repaints when the event changes,
        # so the blink never touches the header line.
        self._guarded(
            "flash-bar",
            text,
            lambda: self.query_one("#flash-bar", FlashBar).update_flash(text),
        )

    def _render_ticker(self) -> None:
        """One-line feed ticker (skipped when nothing new arrived)."""
        if self._last_signal is None:
            return
        sig = self._last_signal
        budget = max(30, self.size.width - 8)
        text = sig.text[: max(20, budget - len(sig.source_name) - 12)]
        line = f"[{sig.source_type}] {sig.source_name}: {text}"
        if line == self._ticker_line:
            return
        self._ticker_line = line
        self.query_one("#ticker", Static).update(line)

    # --- event board -----------------------------------------------------

    def _sync_event_rows(self, events: list[EventCluster]) -> None:
        """Update the event list in place (no clear/rebuild -> no flicker)."""
        lv = self.query_one("#events", ListView)
        budget = max(20, self._content_width("#events", 44) - 4)
        wanted = [ev.event_id for ev in events]

        for event_id in list(self._row_widgets):
            if event_id not in wanted:
                self._row_widgets.pop(event_id).remove()
                self._row_sig.pop(event_id, None)

        for index, ev in enumerate(events):
            style = SEVERITY_STYLE[ev.severity.name]
            n_src = len({s.source_type for s in ev.signals})
            node = ev.exposure[0][0].name if ev.exposure else "no node in range"
            label = self._event_label(ev)
            report = self._council_report(ev.event_id)
            tier = report.level if report else "pending"
            meta = f"ai {tier} - {len(ev.signals)} sig / {n_src} src - {node}"
            flash = ev.event_id in self._flash_ids
            signature = (
                style,
                label,
                meta[:budget],
                round(ev.severity_score, 2),
                self._flash_phase if flash else -1,
            )
            item = self._row_widgets.get(ev.event_id)
            if item is None:
                item = ListItem(
                    Label(self._row_text(signature)), name=ev.event_id
                )
                self._row_widgets[ev.event_id] = item
                lv.insert(index, [item])
                self._row_sig[ev.event_id] = signature
                continue
            if self._row_sig.get(ev.event_id) != signature:
                self._row_sig[ev.event_id] = signature
                item.query_one(Label).update(self._row_text(signature))
            # only reorder when the ranking actually moved
            children = list(lv.children)
            current = children.index(item)
            if current != index:
                if index >= len(children):
                    lv.move_child(item, after=children[-1])
                else:
                    lv.move_child(item, before=children[index])

        selected = self._selected
        if selected and selected in self._order:
            target_index = self._order.index(selected)
            if lv.index != target_index:
                lv.index = target_index

    @staticmethod
    def _row_text(signature: tuple[str, str, str, float, int]) -> str:
        """Label markup for one event row (severity, name, meta, score).

        High-severity rows swap their marker and severity chip with the
        blink phase, so a flashing row is obvious without repainting the
        rest of the list.
        """
        style, label, meta, score, phase = signature
        if phase < 0:
            head = f"[{style}]\u25b8 {label}[/]"
        else:
            chip = (
                "bold white on red"
                if phase % 2 == 0
                else "bold yellow on dark_red"
            )
            marker = "\u25b2" if phase % 2 == 0 else "\u25b8"
            head = f"[{chip}] {marker} {label} [/]"
        return (
            f"{head}\n"
            f"  [dim]{meta}[/] [bold]{score:.2f}[/]"
        )

    def _dashboard_visible(self) -> bool:
        """Are the dashboard's panels on the active screen?

        A report replaces the active screen, and Textual delivers some
        messages (``ListView.Highlighted``, key bindings, timers) after a
        handler has already pushed that report. Anything that touches a
        dashboard widget checks this first, so an in-flight message can
        never query a panel that is not mounted.
        """
        try:
            self.query_one("#outlook", Static)
        except NoMatches:
            return False
        return True

    def _selected_id(self) -> str | None:
        if self._selected and self._selected in self._cluster_by_id:
            return self._selected
        if not self._dashboard_visible():
            return None
        lv = self.query_one("#events", ListView)
        if lv.index is not None and 0 <= lv.index < len(self._order):
            return self._order[lv.index]
        return self._order[0] if self._order else None

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Update chart panels as the cursor moves.

        Highlighted messages arrive asynchronously (``_sync_event_rows``
        sets ``ListView.index``, which posts one), so one can still be in
        flight when a report is pushed - same guard as
        :meth:`on_mouse_move`.
        """
        if len(self.screen_stack) > 1 or not self._dashboard_visible():
            return
        if event.item and event.item.name in self._cluster_by_id:
            self._select_event(self._cluster_by_id[event.item.name])

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Hovering an event previews it across every panel.

        This is the "live preview" path: the pointer (not the keyboard
        cursor) drives which event the charts, precedent panel and
        report target describe. State only changes when the hovered row
        changes, so mouse traffic never repaints the dashboard.
        """
        if len(self.screen_stack) > 1:
            return
        try:
            widget, _region = self.get_widget_at(event.screen_x, event.screen_y)
        except Exception:  # noqa: BLE001 - hit-testing must never break input
            return
        item = widget
        while item is not None and not isinstance(item, ListItem):
            item = item.parent
        name = getattr(item, "name", None)
        if name and name in self._cluster_by_id and name != self._hovered:
            self._hovered = name
            self._selected = name
            self._previewing = True
            self._dirty = True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Click or Enter on an event name opens its preloaded report."""
        if event.item and event.item.name in self._cluster_by_id:
            self._open_report(event.item.name)

    # --- chart panels ----------------------------------------------------

    def _content_width(self, selector: str, fallback: int = 40) -> int:
        """Usable content width of a panel, for size-adaptive charts."""
        try:
            width = self.query_one(selector).region.width
        except NoMatches:
            return fallback
        # minus border (2) and horizontal padding (2)
        return width - 4 if width > 24 else fallback

    def _chart_dims(self, selector: str = "#exposure") -> tuple[int, int]:
        """(label_width, bar_width) fitted to a panel's content width."""
        available = self._content_width(selector)
        label = min(22, max(8, available // 3))
        bar = max(6, available - label - 8)
        return label, bar

    def _guarded(self, key: str, signature: object, render) -> None:
        """Run ``render`` only when content changed (keeps frames stable)."""
        if self._chart_sig.get(key) == signature:
            return
        self._chart_sig[key] = signature
        render()

    def _select_event(self, ev: EventCluster) -> None:
        """Populate every chart + panel for the selected event."""
        self._selected = ev.event_id
        if not self._dashboard_visible():
            return  # a report is open; the next repaint refreshes the panels
        trajectory_w = self._content_width("#trajectory", 32)
        label_w, bar_w = self._chart_dims("#exposure")

        rows = self._trajectory_rows(ev)
        self._guarded(
            "trajectory",
            (ev.event_id, tuple((r[0], r[1], len(r[2])) for r in rows), bar_w),
            lambda: self.query_one("#trajectory", SeverityHistoryChart).update_board(
                rows, width=max(8, trajectory_w - 24)
            ),
        )
        exposure = [(n.name, s) for n, s in ev.exposure[:6]]
        self._guarded(
            "exposure",
            (ev.event_id, tuple(exposure), bar_w, label_w),
            lambda: self.query_one("#exposure", BarChart).update_chart(
                exposure, max_value=1.0, width=bar_w, label_width=label_w
            ),
        )
        analogs = [(a.name, s) for a, s in ev.analogs]
        self._guarded(
            "analogs",
            (ev.event_id, tuple(analogs), bar_w),
            lambda: self.query_one("#analogs", PctBarChart).update_chart(
                analogs, width=bar_w, label_width=label_w
            ),
        )
        outlook = self._outlook_text(ev, self._council_report(ev.event_id))
        self._guarded(
            "outlook",
            (ev.event_id, outlook),
            lambda: self.query_one("#outlook", Static).update(outlook),
        )
        self._render_forecasts(ev)

    def _render_forecasts(self, ev: EventCluster | None) -> None:
        """Leading-indicator panel: nearest forecasts to the selected event.

        Ranked by distance to the event (a Gulf storm leads with Gulf rain),
        with the model's lead time on every row. The panel is deliberately fed
        from the whole forecast lane, not the event's own signals, because a
        single per-node forecast rarely clusters into an event by itself.
        """
        if not self._dashboard_visible():
            return
        origin = ev.centroid if ev is not None else None
        ranked = rank_forecasts(
            origin, list(self._forecasts.values()), limit=FORECAST_ROWS
        )
        rows: list[tuple[str, str, str, float | None]] = []
        for signal in ranked:
            lead = forecast_lead_days(signal)
            km = (
                haversine_km(origin, signal.geo)
                if origin is not None and signal.geo is not None
                else None
            )
            claim = forecast_claim(signal)
            # The panel is already a forecast lane, so the marker is noise.
            if claim.lower().startswith(_FORECAST_PREFIX):
                claim = claim[len(_FORECAST_PREFIX) :].strip()
            rows.append(
                (
                    f"+{lead}d" if lead is not None else "now",
                    claim or "forecast",
                    signal.source_name,
                    round(km, 0) if km is not None else None,
                )
            )
        width = self._content_width("#forecasts", 44)
        self._guarded(
            "forecasts",
            (ev.event_id if ev else None, tuple(rows), width),
            lambda: self.query_one("#forecasts", ForecastPanel).update_forecasts(
                rows, width=width
            ),
        )

    def _trajectory_rows(
        self, selected: EventCluster
    ) -> list[tuple[str, str, list[float]]]:
        """Sparkline rows for the selected event plus the other top events."""
        orch = self._orch
        events = orch.correlator.active_events() if orch else []
        ordered = [selected] + [e for e in events if e.event_id != selected.event_id]
        rows: list[tuple[str, str, list[float]]] = []
        for event in ordered[:5]:
            series = self._history.get(event.event_id, [event.severity_score])
            rows.append((self._event_label(event), event.severity.name, series))
        return rows

    @staticmethod
    def _event_label(ev: EventCluster) -> str:
        """Short display label.

        Prefers the most specific multi-word entity the cluster shares
        ("strait of malacca") over the correlator's single-word title
        ("Strait"), then falls back to the top exposed node.
        """
        counts: dict[str, int] = {}
        for signal in ev.signals:
            for entity in signal.entities:
                counts[entity] = counts.get(entity, 0) + 1
        shared = [
            (name, n)
            for name, n in counts.items()
            if n >= 2 and " " in name
        ]
        if shared:
            name = max(shared, key=lambda kv: (kv[1], len(kv[0])))[0].title()
        elif ev.exposure:
            name = ev.exposure[0][0].name
        else:
            name = ev.title
        name = name.replace("TSMC ", "").replace("Port of ", "")
        return name if len(name) <= 16 else name[:15] + "…"

    def _outlook_text(self, ev: EventCluster, report=None) -> str:
        """AI council read and historical precedent for one event.

        The council block leads: it is the panel's headline output, and a
        small terminal clips the panel's tail. When a brief exists the
        precedent block gives up a market-reaction row so the read stays
        fully visible without scrolling.
        """
        budget = max(24, self._content_width("#outlook", 44) - 4)
        lines = self._council_block(report, budget)
        lines.append("")
        lines.append("[bold green]PRECEDENT: THEN vs NOW[/]")
        if not ev.analogs:
            lines.append("  [dim]no historical analog matched[/]")
        else:
            analog, score = ev.analogs[0]
            lines.append(f"[bold]{analog.name[: budget - 4]}[/]")
            lines.append(f"  match [bold]{score * 100:.0f}%[/] at {analog.date}")
            lines.append(
                f"  then [bold]{analog.duration_days}d[/] vs now "
                f"[bold]{(ev.last_seen - ev.first_seen) / 3600:.1f}h[/]"
            )
            reactions = list(analog.market_reaction.items())
            for key, val in reactions[: 1 if report is not None else 2]:
                lines.append(f"  [dim]{key[: budget - 6]}[/]")
                lines.append(f"    {val[: budget - 4]}")
            lines.append("")
            lines.append("[bold green]EXPOSED NODES[/]")
            for node, node_score in ev.exposure[:3]:
                lines.append(
                    f"  - {node.name[: budget - 12]} [dim]{node_score:.2f}[/]"
                )
        return "\n".join(lines)

    def _council_report(self, event_id: str):
        """The council's brief for an event, or ``None`` if not written yet."""
        orch = self._orch
        return orch.council_report(event_id) if orch else None

    def _council_block(self, report, budget: int) -> list[str]:
        """AI council section of the outlook panel.

        Shows the tier the council assigned, the deterministic lexical
        relevance and route that admitted the event, whether the read came
        from a model or the rule fallback, and the thesis plus critique.
        """
        if report is None:
            return [
                "[bold cyan]AI COUNCIL[/]",
                *(
                    f"[dim]{line}[/]"
                    for line in _wrap(
                        "no brief yet - reads new / escalating events", budget - 2
                    )
                ),
            ]
        origin = "model" if report.model_used else "rules"
        lines = [f"[bold cyan]AI COUNCIL[/] [bold]{report.level}[/]"]
        lines.append(
            f"[dim]{_fit(f'  lex {report.lexical:.2f} · {report.relevance_route} · {origin}', budget)}[/]"
        )
        lines.append(f"[dim]{_fit(f'  {report.trigger}', budget)}[/]")
        lines += _wrap_capped(report.headline, budget - 2, 2)
        lines += [
            f"[dim]{chunk}[/]"
            for chunk in _wrap_capped(report.critique, budget - 2, 2)
        ]
        return lines

    def _render_stats(self, events: list[EventCluster] | None = None) -> None:
        """Refresh the KPI strip (skipped when nothing changed)."""
        if not self._dashboard_visible():
            return
        orch = self._orch
        if events is None:
            events = orch.correlator.active_events() if orch else []
        highest = (
            max((e.severity.name for e in events), key=_band_rank, default="-")
            if events
            else "-"
        )
        alerts = len(orch.alerts) if orch else 0
        ai = self._council_line()
        signature = (
            self._signals_seen,
            len(events),
            alerts,
            highest,
            self.paused,
            self._feed_done,
            self._cycle,
            self._selected,
            self._previewing,
            ai,
        )
        if self._chart_sig.get("stats") == signature:
            return
        self._chart_sig["stats"] = signature
        self.query_one("#stat-strip", StatStrip).update_stats(
            signals=self._signals_seen,
            events=len(events),
            alerts=alerts,
            highest=highest,
            sim_time=self._sim_time,
            paused=self.paused,
            feed_done=self._feed_done,
            feed_name=(
                f"synthetic c{self._cycle}"
                if self._cycle
                else {
                    SYNTHETIC_FEED: "synthetic",
                    LIVE_FEED: "LIVE news+weather",
                }.get(self.feed, self.feed)
            ),
            focus=self._event_label(self._cluster_by_id[self._selected])
            if self._selected in self._cluster_by_id
            else "-",
            previewing=self._previewing,
            ai=ai,
        )

    def _council_line(self) -> str:
        """One-line AI status for the KPI strip, fitted to the terminal."""
        orch = self._orch
        council = orch.council if orch else None
        if council is None:
            return "disabled"
        line = f"{council.status_line()} · {council.tally()}"
        # the strip prefixes this with " AI COUNCIL  · ", so budget for it
        return _fit(line, max(24, self.size.width - 16))

    # --- volume + mix charts ---------------------------------------------

    def _flow_bucket_rows(self) -> list[tuple[float, int]]:
        """Signal counts per equal time bucket across the feed so far."""
        times = self._signal_times
        if len(times) < 2:
            return []
        start, end = times[0], times[-1]
        span = max(end - start, 1.0)
        buckets = [0] * self._flow_buckets
        for ts in times:
            index = min(
                self._flow_buckets - 1, int((ts - start) / span * self._flow_buckets)
            )
            buckets[index] += 1
        step = span / self._flow_buckets
        return [(start + i * step, count) for i, count in enumerate(buckets)]

    def _level_mix_rows(self, events: list[EventCluster]) -> list[tuple[str, float]]:
        """Share of active events per severity band."""
        total = len(events) or 1
        counts = [
            (band.name, sum(1 for e in events if e.severity.name == band.name))
            for band in reversed(list(Severity))
        ]
        return [
            (f"{name:<8} {n}", n / total) for name, n in counts if n
        ]

    def _render_flow_charts(self, events: list[EventCluster]) -> None:
        """Volume histogram plus the event level mix."""
        buckets = self._flow_bucket_rows()
        width = max(16, self._content_width("#flow", 44) - 4)
        self._guarded(
            "flow",
            (tuple(buckets), width),
            lambda: self.query_one("#flow", TimeFlowChart).update_flow(
                buckets, width=width
            ),
        )
        levels = self._level_mix_rows(events)
        l_label, l_bar = self._chart_dims("#level-mix")
        self._guarded(
            "level-mix",
            (tuple(levels), l_bar),
            lambda: self.query_one("#level-mix", PctBarChart).update_chart(
                levels, width=l_bar, label_width=l_label
            ),
        )

    def _redraw_charts(self) -> None:
        """Force a full chart repaint after a resize (widths changed)."""
        self._chart_sig.clear()
        self._dirty = True
        try:
            self._render_frame()
        except NoMatches:
            return

    def _panel_height(self, selector: str, fallback: int = 10) -> int:
        """Usable content height of a panel."""
        try:
            height = self.query_one(selector).region.height
        except NoMatches:
            return fallback
        return height - 4 if height > 6 else fallback

    def _refresh_watchlist(self) -> None:
        """Watchlist for the active vertical: segment groups, then leftovers.

        Segments are the expanded view (foundry, equipment, memory, ...);
        any ticker not covered by a segment still gets listed below them,
        packed to the panel width, so nothing is silently dropped.
        """
        if not self._dashboard_visible():
            return
        spec = self.markets[self.market_key]
        budget = max(4, self._panel_height("#watchlist", 10))
        width = max(16, self._content_width("#watchlist", 44) - 1)
        lines: list[str] = []

        def add_rows(prefix: str, indent: str, tickers: list[str]) -> None:
            """Wrap tickers to the panel width: first row prefixed, rest
            indented, so a long segment never spills past the border."""
            per_row = max(3, (width - len(indent)) // 6)
            for start in range(0, len(tickers), per_row):
                if len(lines) >= budget:
                    return
                chunk = tickers[start : start + per_row]
                lead = prefix if start == 0 else indent
                body = " ".join(f"{ticker:<5}" for ticker in chunk)
                lines.append(lead + body.rstrip())

        covered: set[str] = set()
        for name, tickers in spec.segments:
            if len(lines) >= budget - 1:
                break
            add_rows(
                f"[dim]{name[:9]:<9}[/] ",
                " " * 10,
                list(tickers),
            )
            covered.update(tickers)
        rest = [t for t in spec.watchlist if t not in covered]
        add_rows("  ", "  ", rest)
        self.query_one("#watchlist", Static).update(
            f"[bold green]WATCHLIST[/] [dim]{spec.index} - "
            f"{len(spec.watchlist)} names[/]\n\n" + "\n".join(lines)
        )

    # --- responsive ------------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        """Drop optional panels on narrow screens, stack on very narrow."""
        width, height = event.size.width, event.size.height
        self.screen.set_class(width < 92, "stacked")
        self.screen.set_class(height < 36, "short")
        self._redraw_charts()

    # --- actions ---------------------------------------------------------

    def action_toggle_pause(self) -> None:
        """Freeze/advance the feed."""
        self.paused = not self.paused
        self._render_stats()

    def _open_report(self, event_id: str) -> None:
        """Display a preloaded report; build only if the cache is cold."""
        cluster = self._cluster_by_id.get(event_id)
        if cluster is None:
            return
        page = self._reports.get(event_id)
        if page is None:  # cold cache (e.g. direct call before ingest)
            page = self._preload_reports(cluster)
        self.push_screen(ReportScreen(f"{cluster.severity.name}: {cluster.title}", page))

    def action_report(self) -> None:
        """Open the report for the highlighted event."""
        eid = self._selected_id()
        if eid:
            self._open_report(eid)

    def action_situation(self) -> None:
        """Open the full situation report, assembled from cached pages."""
        self.push_screen(
            ReportScreen("SITUATION REPORT", self._full_report())
        )

    def _full_report(self) -> str:
        """Index page plus every preloaded event page (no work on click)."""
        parts = [self._reports.index or "# Situation report\n\nNo active events."]
        for event_id in self._order:
            page = self._reports.get(event_id)
            if page:
                parts.append("---")
                parts.append(page)
        return "\n\n".join(parts)

    def _preload_reports(self, cluster: EventCluster) -> str:
        """Cache this event's page and refresh the index; returns the page."""
        orch = self._orch
        notified = bool(
            orch
            and any(a.event.event_id == cluster.event_id for a in orch.alerts)
        )
        page = self._reports.preload(
            cluster,
            as_of_ts=self._sim_time.timestamp() if self._sim_time else None,
            notified=notified,
            council=self._council_report(cluster.event_id),
        )
        if orch is not None:
            self._reports.preload_index(
                orch.correlator.active_events(),
                as_of_ts=self._sim_time.timestamp() if self._sim_time else None,
                notified_ids=frozenset(a.event.event_id for a in orch.alerts),
            )
        return page

    def action_restart(self) -> None:
        """Reset the pipeline and replay from the top."""
        self._restart_pipeline()

    def on_shutdown_request(self) -> None:
        """Release the council pool when the app is asked to shut down."""
        self._close_council()

    def on_unmount(self) -> None:
        """Release the council pool when the dashboard is torn down.

        A live council holds a thread pool that the interpreter joins at exit,
        so an unclosed pool makes quitting look like a hang.
        """
        self._close_council()

    def _close_council(self) -> None:
        """Close the current orchestrator's council, if it has one."""
        orchestrator, self._orch = self._orch, None
        if orchestrator is not None:
            orchestrator.close()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Switching market or feed rebuilds the pipeline."""
        # Select widgets emit Changed when their value is first set, so guard
        # against the no-op that would otherwise rebuild the pipeline twice as
        # the dashboard mounts (and join a council pool that is still working).
        if (
            event.select.id == "market-select"
            and event.value
            and str(event.value) != self.market_key
        ):
            self.market_key = str(event.value)
            self._restart_pipeline()
        elif (
            event.select.id == "feed-select"
            and event.value
            and str(event.value) != self.feed
        ):
            self.feed = str(event.value)
            self._restart_pipeline()

    def _restart_pipeline(self) -> None:
        spec = self.markets[self.market_key]
        previous = self._orch
        self._orch = self._build_orch()
        if previous is not None and previous.council is not None:
            previous.council.close()
        self._reports = ReportCache(
            market_label=spec.label,
            market_index=spec.index,
            watchlist=spec.watchlist,
        )
        self._cluster_by_id.clear()
        self._history.clear()
        self._order.clear()
        self._selected = None
        self._signals_seen = 0
        self._sim_time = None
        self._feed_done = False
        self._signal_iter = self._build_signals()
        self._row_widgets.clear()
        self._row_sig.clear()
        self._chart_sig.clear()
        self._preload_dirty.clear()
        self._signal_times.clear()
        self._forecasts.clear()
        self._last_signal = None
        self._ticker_line = ""
        self._hovered = None
        self._previewing = False
        self._council_revision = 0
        self._refresh_watchlist()
        self._render_stats([])
        self.query_one("#ticker", Static).update(
            f"loading feed: {'synthetic' if self.feed == SYNTHETIC_FEED else self.feed}"
        )
        self.query_one("#events", ListView).clear()
        self.query_one("#outlook", Static).update(
            "[bold green]PRECEDENT: THEN vs NOW[/]\n\n  [dim]awaiting events…[/]"
        )
        self.query_one("#forecasts", ForecastPanel).update_forecasts([])
        self._redraw_charts()


def _band_rank(name: str) -> int:
    """Sort severity band names by urgency (used for the PEAK KPI)."""
    try:
        return int(Severity[name])
    except KeyError:
        return 0


def main() -> None:
    """CLI entry: uv run watchtower [--feed synthetic|suez_2021]."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--feed",
        "--scenario",
        dest="feed",
        default=SYNTHETIC_FEED,
        help="'synthetic' or a replay scenario stem",
    )
    parser.add_argument("--market", default="semicon")
    parser.add_argument(
        "--speed",
        type=float,
        default=DEFAULT_CADENCE_S,
        help="seconds between signals",
    )
    parser.add_argument("--seed", type=int, default=7, help="synthetic seed")
    parser.add_argument(
        "--no-loop",
        action="store_true",
        help="stop after one synthetic pass instead of rolling the feed",
    )
    parser.add_argument(
        "--log",
        default="",
        help="write logs to this file instead of stderr (keeps the TUI clean)",
    )
    args = parser.parse_args()
    # stderr output would paint over the Textual screen; route logs away from
    # the terminal unless the caller asked for a file.
    logging.basicConfig(
        filename=args.log or None,
        level=logging.INFO if args.log else logging.CRITICAL,
        force=True,
    )
    WatchtowerApp(
        market_key=args.market,
        feed=args.feed,
        cadence_s=args.speed,
        seed=args.seed,
        loop_feed=not args.no_loop,
    ).run()


if __name__ == "__main__":
    main()
