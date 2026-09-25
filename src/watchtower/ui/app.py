"""Watchtower TUI: three-column market monitor over the replay feed.

Keys: space pause/resume, enter event details, r restart, q quit.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView, Markdown, Select, Static

from watchtower.agents.orchestrator import Orchestrator
from watchtower.config import REPLAY_DIR, SCENARIO_EXCLUSIONS, load_markets
from watchtower.models import EventCluster, Severity, Signal
from watchtower.sources import replay_signals

SEVERITY_STYLE: dict[Severity, str] = {
    Severity.LOW: "dim",
    Severity.GUARDED: "blue",
    Severity.ELEVATED: "yellow",
    Severity.HIGH: "dark_orange",
    Severity.SEVERE: "bold red",
}

DEFAULT_CADENCE_S = 1.0


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%MZ")


class EventDetailScreen(ModalScreen):
    """Full audit view: severity breakdown, three lens briefs, evidence."""

    def __init__(self, event: EventCluster) -> None:
        super().__init__()
        self.event = event

    def compose(self) -> ComposeResult:
        """Render the scrollable audit document for one event."""
        yield Container(
            Markdown(self._document(), id="detail-md"),
            Static("esc close", id="detail-hint"),
            id="detail",
        )

    def _document(self) -> str:
        ev = self.event
        parts = [
            f"# {ev.title}",
            f"**{ev.severity.name}** composite {ev.severity_score:.2f}"
            f" — {len(ev.signals)} signals,"
            f" first seen {_fmt_ts(ev.first_seen)} UTC",
            "",
            "## Severity breakdown",
            "| component | value | weight | rationale |",
            "|---|---|---|---|",
            *[
                f"| {c.name} | {c.value:.2f} | {c.weight:.2f} | {c.rationale} |"
                for c in ev.components
            ],
            "",
            "## Health",
            ev.briefs.get("health", "—"),
            "",
            "## Wealth",
            ev.briefs.get("wealth", "—"),
            "",
            "## Insurance",
            ev.briefs.get("insurance", "—"),
            "",
            "## Evidence trail",
            *[
                f"- `{_fmt_ts(s.ts)}` [{s.source_type}] "
                f"{s.source_name}: {s.text}" + (f" — {s.url}" if s.url else "")
                for s in ev.signals
            ],
        ]
        return "\n".join(parts)

    def on_key(self, event: events.Key) -> None:
        """Escape closes the audit view."""
        if event.key == "escape":
            self.app.pop_screen()


class WatchtowerApp(App):
    """Main dashboard: market selector + three monitoring columns."""

    TITLE = "WATCHTOWER"
    CSS = """
    #top-bar { height: 3; padding: 0 1; border-bottom: heavy $accent;
               align: left middle; }
    #market-select { width: 24; }
    #scenario-select { width: 34; margin-left: 1; }
    #clock { margin-left: 2; color: $text-muted; }
    #alerts { margin-left: 2; color: $warning; }
    #main-grid { layout: horizontal; height: 1fr; padding: 1; }
    .column { width: 1fr; height: 100%; margin: 0 1; }
    .panel { border: solid $accent; padding: 1; height: 100%; }
    #right-column { width: 1fr; height: 100%; margin: 0 1; }
    .sub-panel { border: solid $accent; padding: 1; height: 1fr; }
    #ticker { height: 2; padding: 0 1; color: $text-muted; }
    #chokepoints { height: 2; padding: 0 1; color: $text-muted;
                  border-top: solid $accent; }
    ListView { border: solid $accent; }
    #detail { width: 90%; height: 90%; border: heavy $accent;
              background: $surface; padding: 1; }
    #detail-md { height: 1fr; }
    #detail-hint { color: $text-muted; text-align: right; }
    """

    BINDINGS: ClassVar[list] = [
        ("space", "toggle_pause", "pause/resume"),
        ("enter", "details", "details"),
        ("r", "restart", "restart"),
        ("q", "quit", "quit"),
    ]

    def __init__(
        self,
        market_key: str = "semicon",
        scenario: str = "suez_2021",
        cadence_s: float = DEFAULT_CADENCE_S,
    ) -> None:
        super().__init__()
        self.markets = load_markets()
        self.market_key = market_key
        self.scenario = scenario
        self.cadence_s = cadence_s
        self.paused = False
        self._signal_iter: Iterator[Signal] = iter(())
        self._orch: Orchestrator | None = None
        self._order: list[str] = []
        self._cluster_by_id: dict[str, EventCluster] = {}

    def _build_orch(self) -> Orchestrator:
        exclude = SCENARIO_EXCLUSIONS.get(self.scenario, frozenset())
        return Orchestrator(self.markets[self.market_key], exclude_analogs=exclude)

    def compose(self) -> ComposeResult:
        """Lay out the top bar, three columns, and the signal ticker."""
        options = [(spec.label, key) for key, spec in self.markets.items()]
        scenarios = sorted(p.stem for p in REPLAY_DIR.glob("*.jsonl"))
        scenario_options = [(s, s) for s in scenarios]
        yield Horizontal(
            Label("WATCHTOWER  "),
            Select(options, value=self.market_key, id="market-select"),
            Select(
                scenario_options,
                value=self.scenario,
                id="scenario-select",
                prompt="event",
            ),
            Static("", id="clock"),
            Static("", id="alerts"),
            id="top-bar",
        )
        yield Horizontal(
            Container(ListView(id="events"), classes="column"),
            Container(
                Static(
                    "[bold green]Historical Impact[/]\n\nno event selected",
                    id="impact",
                    classes="panel",
                ),
                classes="column",
            ),
            Vertical(
                Static("", id="watchlist", classes="sub-panel"),
                Static("", id="severity", classes="sub-panel"),
                id="right-column",
                classes="column",
            ),
            id="main-grid",
        )
        yield Static("feed idle", id="ticker")
        yield Static("", id="chokepoints")
        yield Footer()

    def on_mount(self) -> None:
        """Start the replay pump once the UI is up."""
        self._restart_pipeline()
        self.run_worker(self._pump(), exclusive=True)

    # --- replay driver -------------------------------------------------

    async def _pump(self) -> None:
        """Feed signals through the pipeline at demo cadence."""
        for signal in self._signal_iter:
            while self.paused:
                await asyncio.sleep(0.1)
            if self._orch is None:
                raise RuntimeError("pipeline not initialized")
            cluster = self._orch.process(signal)
            self._cluster_by_id[cluster.event_id] = cluster
            try:
                self._ingest_visual(signal, cluster)
            except NoMatches:
                break
            await asyncio.sleep(self.cadence_s)
        self.query_one("#ticker", Static).update("replay complete")

    def _ingest_visual(self, signal: Signal, cluster: EventCluster) -> None:
        """Reflect one processed signal into the dashboard widgets."""
        self.query_one("#clock", Static).update(f"as of {_fmt_ts(signal.ts)} UTC")
        text = signal.text[:107] + ("…" if len(signal.text) > 107 else "")
        self.query_one("#ticker", Static).update(f"{signal.source_type} | {text}")
        if self._orch is None:
            return
        active = self._orch.correlator.active_events()
        self._order = [c.event_id for c in active]
        self._refresh_event_list(active)
        self._refresh_chokepoints()
        if self._order:
            target = self._selected_id() or self._order[0]
            self._select_event(self._cluster_by_id[target])

    # --- event list ----------------------------------------------------

    def _refresh_event_list(self, events: list[EventCluster]) -> None:
        """Rebuild the ranked event list, preserving the selection."""
        lv = self.query_one("#events", ListView)
        lv.clear()
        for ev in events:
            style = SEVERITY_STYLE[ev.severity]
            n_src = len({s.source_type for s in ev.signals})
            lv.append(
                ListItem(
                    Label(
                        f"[{style}]{ev.severity.name:<8}[/] "
                        f"{ev.title}  [dim]{ev.severity_score:.2f}[/]\n"
                        f"  {len(ev.signals)} signals / {n_src} sources"
                        + (
                            f"  ▸ {ev.exposure[0][0].name} {ev.exposure[0][1]:.2f}"
                            if ev.exposure
                            else ""
                        ),
                    ),
                    name=ev.event_id,
                )
            )

    def _selected_id(self) -> str | None:
        lv = self.query_one("#events", ListView)
        if lv.index is None or lv.index >= len(self._order):
            return self._order[0] if self._order else None
        return self._order[lv.index]

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Update right-hand panels as the cursor moves."""
        if event.item and event.item.name in self._cluster_by_id:
            self._select_event(self._cluster_by_id[event.item.name])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on an event opens the audit view."""
        if event.item and event.item.name in self._cluster_by_id:
            self.push_screen(EventDetailScreen(self._cluster_by_id[event.item.name]))

    # --- right-hand panels --------------------------------------------

    def _select_event(self, ev: EventCluster) -> None:
        """Populate impact + severity panels for the selected event."""
        analog_lines = ["[bold green]Historical Impact[/]\n"]
        for analog, score in ev.analogs:
            analog_lines.append(f"• {analog.name} [{score:.2f}]")
            analog_lines.append(f"  {next(iter(analog.market_reaction.values()), '')}")
        if not ev.analogs:
            analog_lines.append("  no precedent found")
        analog_lines.append("")
        analog_lines.append("[bold green]Exposed nodes[/]\n")
        for node, score in ev.exposure[:5]:
            analog_lines.append(
                f"• {node.name} [dim]({node.kind}, crit {node.criticality:.2f})[/]"
                f" — {score:.2f}"
            )
        if not ev.exposure:
            analog_lines.append("  none in range")
        self.query_one("#impact", Static).update("\n".join(analog_lines))
        style = SEVERITY_STYLE[ev.severity]
        lines = [
            "[bold green]Severity[/]\n",
            f"[{style}]{ev.severity.name}[/]  composite {ev.severity_score:.2f}",
            "",
        ]
        for c in ev.components:
            lines.append(f"{c.name:<13} [dim]{c.rationale}[/]")
        lines.append("")
        lines.append(f"alerts: {len(self._orch.alerts) if self._orch else 0}")
        self.query_one("#severity", Static).update("\n".join(lines))

        if self._orch is not None:
            self.query_one("#alerts", Static).update(
                f"alerts: {len(self._orch.alerts)}"
            )

    def _refresh_chokepoints(self) -> None:
        """WorldMonitor-style live chokepoint strip, top 5 exposures."""
        if self._orch is None:
            return
        parts = [
            f"{name} {score:.2f}" for name, score in self._orch.chokepoint_status()[:5]
        ]
        self.query_one("#chokepoints", Static).update(
            "CHOKEPOINTS  " + "   ".join(parts)
        )

    def _refresh_watchlist(self) -> None:
        spec = self.markets[self.market_key]
        tickers = "\n".join(f"{i}. {t}" for i, t in enumerate(spec.watchlist, 1))
        self.query_one("#watchlist", Static).update(
            f"[bold green]Stocks to watch[/]  ({spec.index})\n\n{tickers}"
        )

    # --- actions --------------------------------------------------------

    def action_toggle_pause(self) -> None:
        """Freeze/advance the replay."""
        self.paused = not self.paused

    def action_details(self) -> None:
        """Open the audit view for the highlighted event."""
        eid = self._selected_id()
        if eid and eid in self._cluster_by_id:
            self.push_screen(EventDetailScreen(self._cluster_by_id[eid]))

    def action_restart(self) -> None:
        """Reset the pipeline and replay from the top."""
        self._restart_pipeline()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Switching markets rebuilds the pipeline for that vertical."""
        if event.select.id == "market-select" and event.value:
            self.market_key = str(event.value)
            self._restart_pipeline()
        elif event.select.id == "scenario-select" and event.value:
            self.scenario = str(event.value)
            self._restart_pipeline()

    def _restart_pipeline(self) -> None:
        self._orch = self._build_orch()
        self._cluster_by_id.clear()
        self._order.clear()
        path = REPLAY_DIR / f"{self.scenario}.jsonl"
        self._signal_iter = replay_signals(path)
        self._refresh_watchlist()
        self.query_one("#chokepoints", Static).update("")
        self.query_one("#events", ListView).clear()
        self.query_one("#impact", Static).update(
            "[bold green]Historical Impact[/]\n\nno event selected"
        )
        self.query_one("#severity", Static).update("[bold green]Severity[/]\n\n—")


def main() -> None:
    """CLI entry: uv run watchtower [--scenario suez_2021]."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="suez_2021")
    parser.add_argument("--market", default="semicon")
    parser.add_argument(
        "--speed",
        type=float,
        default=DEFAULT_CADENCE_S,
        help="seconds between signals",
    )
    args = parser.parse_args()
    WatchtowerApp(
        market_key=args.market,
        scenario=args.scenario,
        cadence_s=args.speed,
    ).run()


if __name__ == "__main__":
    main()
