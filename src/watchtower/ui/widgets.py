"""Custom chart widgets for the Watchtower TUI.

Every chart is a Rich renderable so it is plain-text, testable, and
themeable. No extra dependencies: bar charts are drawn with block
glyphs and the trajectory chart is a hand-rolled braille/block
sparkline.

Two bar styles live here:

- ``BarChart``   — absolute bars scaled against the max value, for
  "top N exposures" boards where the longest bar is the story.
- ``PctBarChart`` — percentage-filled bars (0-100% of width), for
  analog match confidence where the number itself is the signal.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

# Severity band -> rich color, shared by the chart panels.
SEVERITY_STYLE: dict[str, str] = {
    "LOW": "dim",
    "GUARDED": "cyan",
    "ELEVATED": "yellow",
    "HIGH": "dark_orange",
    "SEVERE": "bold red",
}

_BULLET = "|"
_BLOCKS = "▁▂▃▄▅▆▇█"


def bar_line(
    label: str,
    value: float,
    max_value: float,
    width: int = 30,
    label_width: int = 24,
    color: str = "green",
) -> Text:
    """One Rich Text line: label + bar + numeric value, truncated safely."""
    frac = max(0.0, min(1.0, value / max_value)) if max_value > 0 else 0.0
    filled = round(frac * max(1, width))
    return Text.assemble(
        (f"{label:<{label_width}.{label_width}}", "bold"),
        ("█" * filled, color),
        ("░" * (max(1, width) - filled), "dim"),
        (f" {value:.2f}", "bold"),
    )


def pct_bar_line(
    label: str, pct: float, width: int = 28, label_width: int = 24
) -> Text:
    """One Rich Text line with a percentage-filled bar (no rescaling)."""
    frac = max(0.0, min(1.0, pct))
    filled = round(frac * max(1, width))
    color = "red" if frac >= 0.6 else ("yellow" if frac >= 0.35 else "green")
    return Text.assemble(
        (f"{label:<{label_width}.{label_width}}", "bold"),
        ("█" * filled, color),
        ("░" * (max(1, width) - filled), "dim"),
        (f" {pct * 100:3.0f}%", "bold"),
    )


def sparkline_text(series: list[float], width: int = 48) -> str:
    """Block-glyph sparkline right-padded to ``width`` (peaks right)."""
    if not series:
        return ""
    width = max(2, width)
    vals = series[-width:]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    glyphs = "".join(
        _BLOCKS[min(len(_BLOCKS) - 1, int((v - lo) / span * (len(_BLOCKS) - 1)))]
        for v in vals
    )
    pad = max(0, width - len(glyphs))
    return " " * pad + glyphs


def _block(level: float) -> str:
    """One column cell for a fractional 0-1 height (partial blocks at top)."""
    if level <= 0:
        return " "
    if level >= 1:
        return "█"
    return _BLOCKS[max(0, min(len(_BLOCKS) - 1, int(level * len(_BLOCKS))))]


def block_columns(
    counts: list[float], width: int = 60, height: int = 3
) -> list[str]:
    """Resample counts into ``width`` columns and render ``height`` rows."""
    if not counts:
        return [" " * width for _ in range(height)]
    if len(counts) > width:
        merged: list[float] = []
        for i in range(width):
            lo = int(i * len(counts) / width)
            hi = max(lo + 1, int((i + 1) * len(counts) / width))
            merged.append(sum(counts[lo:hi]))
        counts = merged
    else:
        counts = list(counts) + [0.0] * (width - len(counts))
    top = max(counts) or 1.0
    rows: list[str] = []
    for row in range(height):
        line = ""
        for value in counts:
            level = value / top * height
            line += _block(level - (height - 1 - row))
        rows.append(line)
    return rows


class TimeFlowChart(Static):
    """Signal volume over time as block columns (histogram)."""

    def __init__(self, title: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self._title = title
        self.flat_content: object = Text("")

    def update_flow(
        self,
        buckets: list[tuple[float, int]],
        width: int = 56,
        height: int = 2,
    ) -> None:
        """buckets: (bucket_start_ts, count) oldest first."""
        head = Text(self._title, style="bold green")
        if not buckets:
            group = Group(head, Text("\n  waiting for signals...", style="dim"))
            self.flat_content = group
            super().update(group)
            return
        rows = block_columns([float(c) for _, c in buckets], width, height)
        body = Text("\n")
        for row in rows:
            body.append("  " + row + "\n", style="cyan")
        total = sum(c for _, c in buckets)
        peak = max(c for _, c in buckets)
        first = datetime.fromtimestamp(buckets[0][0], UTC).strftime("%m-%d %H:%M")
        last = datetime.fromtimestamp(buckets[-1][0], UTC).strftime("%m-%d %H:%M")
        # two short lines so the caption fits narrow panels
        body.append(f"\n  {first} -> {last} UTC", style="dim")
        body.append(
            f"\n  {total} signals, peak {peak}/bucket", style="dim"
        )
        group = Group(head, body)
        self.flat_content = group
        super().update(group)


class BarChart(Static):
    """Vertical list of absolute-scaled bars with labels + values."""

    def __init__(self, title: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self._title = title
        self.flat_content: object = Text("")

    def update_chart(
        self,
        rows: list[tuple[str, float]],
        max_value: float | None = None,
        width: int = 30,
        label_width: int = 24,
    ) -> None:
        """Re-render the chart from (label, value) rows."""
        top = (
            max_value
            if max_value is not None
            else (max((v for _, v in rows), default=1.0) or 1.0)
        )
        lines = Text(self._title, style="bold green") if self._title else Text()
        for label, value in rows:
            lines.append("\n")
            lines.append_text(
                bar_line(label, value, top, width=width, label_width=label_width)
            )
        if not rows:
            lines.append_text(Text("\n  no data yet", style="dim"))
        self.flat_content = lines
        super().update(lines)


class PctBarChart(Static):
    """Vertical list of 0-100% filled bars (raw percentages, not scaled)."""

    def __init__(self, title: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self._title = title
        self.flat_content: object = Text("")

    def update_chart(
        self,
        rows: list[tuple[str, float]],
        width: int = 28,
        label_width: int = 24,
    ) -> None:
        """Re-render the chart from (label, 0-1 fraction) rows."""
        lines = Text(self._title, style="bold green") if self._title else Text()
        for label, pct in rows:
            lines.append("\n")
            lines.append_text(
                pct_bar_line(label, pct, width=width, label_width=label_width)
            )
        if not rows:
            lines.append_text(Text("\n  no data yet", style="dim"))
        self.flat_content = lines
        super().update(lines)


class ForecastPanel(Static):
    """Leading indicators: the nearest forecast signals and their lead times.

    Weather and hazard models project hazards days before they are observed,
    so this panel is the dashboard's look-ahead lane. Rows are ranked by
    distance to the selected event (see ``report.rank_forecasts``); each row
    leads with the model's lead time, so a ``+3d`` is never read as something
    that has already happened.
    """

    def __init__(self, title: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self._title = title
        self.flat_content: object = Text("")

    def update_forecasts(
        self,
        rows: list[tuple[str, str, str, float | None]],
        width: int = 44,
    ) -> None:
        """rows: (lead, claim, source_name, km-to-event or None)."""
        head = Text(self._title or "EARLY INDICATORS", style="bold green")
        body = Text("\n")
        if not rows:
            idle = "  no forecast signals in this feed"
            body.append(idle[: max(8, width)], style="dim")
        for lead, claim, source, km in rows:
            tail = f" {source}"
            if km is not None:
                tail += f" {km:.0f}km"
            budget = max(6, width - 7 - len(tail))
            body.append(
                f"  {lead:>4} ",
                style="bold cyan" if lead != "now" else "dim",
            )
            body.append(f"{claim[:budget]:<{budget}}", style="bold")
            body.append(tail + "\n", style="dim")
        group = Group(head, body)
        self.flat_content = group
        super().update(group)


class SeverityHistoryChart(Static):
    """Per-event severity sparklines: the escalation board."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.flat_content: object = Text("")

    def update_board(
        self,
        rows: list[tuple[str, str, list[float]]],
        width: int = 32,
    ) -> None:
        """rows: (label, severity_name, score-series) per active event."""
        head = Text("SEVERITY TRAJECTORY", style="bold green")
        body = Text("\n")
        if not rows:
            body.append("  collecting signals...", style="dim")
        for label, sev, series in rows:
            spark = sparkline_text(series, width)
            body.append(f"  {label:<12.12} ", style="bold")
            body.append(spark, style=SEVERITY_STYLE.get(sev, "dim"))
            body.append(f" {series[-1] if series else 0.0:.2f}\n", style="bold")
        group = Group(head, body)
        self.flat_content = group
        super().update(group)


class FlashBar(Static):
    """Steady one-line banner naming the most urgent HIGH/SEVERE event.

    The banner deliberately does not blink: a pulsing header is hard to
    read and competes with the flashing event rows. Severity is carried
    by colour instead, and this widget only repaints when the headline
    event itself changes.
    """

    ALERT_STYLE = "bold white on red"

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.flat_content: object = Text("")

    def update_flash(self, text: str) -> None:
        """Render the alert banner (static styling, no blink phase)."""
        line = Text(f" {text} ", style=self.ALERT_STYLE)
        self.flat_content = line
        super().update(line)


class StatStrip(Static):
    """Centered headline stats: signals, events, alerts, sim clock, AI."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.flat_content: object = Text("")

    def update_stats(
        self,
        *,
        signals: int = 0,
        events: int = 0,
        alerts: int = 0,
        highest: str = "-",
        sim_time: datetime | None = None,
        paused: bool = False,
        feed_done: bool = False,
        feed_name: str = "",
        focus: str = "",
        previewing: bool = False,
        ai: str = "",
    ) -> None:
        """Render the KPI strip, including the live state of the AI council."""
        t = sim_time.strftime("%Y-%m-%d %H:%M UTC") if sim_time else "-"
        status = (
            "PAUSED"
            if paused
            else ("REPLAY DONE" if feed_done else "LIVE REPLAY")
        )
        color = "yellow" if paused else ("dim" if feed_done else "green")
        line1 = Text.assemble(
            (f" SIGNALS {signals} ", "bold cyan"),
            (f" {_BULLET} ", "dim"),
            (f"EVENTS {events} ", "bold magenta"),
            (f" {_BULLET} ", "dim"),
            (f"ALERTS {alerts} ", "bold red"),
            (f" {_BULLET} ", "dim"),
            (f"PEAK {highest} ", "bold yellow"),
        )
        line2 = Text.assemble(
            (f" SIM CLOCK {t} ", "bold"),
            (f" {_BULLET} ", "dim"),
            (f"{status} ", color),
            (f" {_BULLET} ", "dim"),
            (f"FEED {feed_name or '-'} ", "dim"),
        )
        marker = "HOVER" if previewing else "SELECTED"
        line3 = Text.assemble(
            (f" DASHBOARD {marker} ", "bold cyan"),
            (f" {_BULLET} ", "dim"),
            (focus or "-", "bold magenta"),
        )
        # The AI line is what makes the model's work visible in the demo:
        # it shows whether the council is running a model or its fallback,
        # how many calls it has made, and how many briefs it has written.
        online = "AI on" in ai
        line4 = Text.assemble(
            (" AI COUNCIL ", "bold green" if online else "bold yellow"),
            (f" {_BULLET} ", "dim"),
            (ai or "off", "bold" if online else "dim"),
        )
        group = Group(line1, line2, line3, line4)
        self.flat_content = group
        super().update(group)
