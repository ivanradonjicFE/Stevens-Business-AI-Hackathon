"""Headless TUI layout report: geometry, visibility, and chart text.

Boots the dashboard without a terminal, lets the feed prefill, then
prints each panel's region, display state, and rendered content so
layout/chart changes can be reviewed (and regressions caught) quickly.

    uv run python scripts/preview_tui.py --size 140x46
    uv run python scripts/preview_tui.py --size 90x30 --svg preview.svg
"""

from __future__ import annotations

import argparse
import asyncio
import io

from rich.console import Console
from rich.text import Text
from textual.widgets import Label, Select

from watchtower.agents import llm
from watchtower.ui.app import WatchtowerApp

PANELS = (
    "#title",
    "#controls",
    "#stat-strip",
    "#events",
    "#trajectory",
    "#exposure",
    "#analogs",
    "#outlook",
    "#forecasts",
    "#watchlist",
    "#flow",
    "#level-mix",
    "#ticker",
)


WIDE = 400  # render unwrapped so overflow vs the panel is measurable


def _plain(renderable, width: int = WIDE) -> str:
    """Render a Rich renderable (or markup string) to plain text."""
    if isinstance(renderable, str):
        renderable = Text.from_markup(renderable)
    console = Console(
        width=width, record=True, file=io.StringIO(), force_terminal=False
    )
    console.print(renderable)
    return console.export_text().rstrip()


async def report(
    feed: str,
    width: int,
    height: int,
    wait: float,
    svg_path: str | None,
    offline: bool = False,
) -> None:
    """Boot headless and print the layout report."""
    if offline:
        # Layout checks want the deterministic rule path: a live model adds
        # network round-trips and, worse, in-flight calls the interpreter
        # joins at exit, so a sweep would crawl.
        llm._credential = lambda name: None
        llm._key_file = lambda: None
    app = WatchtowerApp(feed=feed, cadence_s=0.02)
    try:
        await _layout(app, feed, width, height, wait, svg_path)
    finally:
        # A live council holds a thread pool that the interpreter joins at
        # exit, so leaving it open makes this script appear to hang.
        if app._orch is not None:
            app._orch.close()


async def _layout(
    app: WatchtowerApp,
    feed: str,
    width: int,
    height: int,
    wait: float,
    svg_path: str | None,
) -> None:
    """Run the app headless and print each panel's geometry and content."""
    async with app.run_test(size=(width, height)) as pilot:
        await pilot.pause()
        await asyncio.sleep(wait)

        print(f"feed={feed} size={width}x{height} signals={app._signals_seen}")
        print(
            f"events={len(app._order)} alerts="
            f"{len(app._orch.alerts) if app._orch else 0} "
            f"stacked={'stacked' in app.screen.classes} "
            f"short={'short' in app.screen.classes}"
        )
        print("-" * width)
        for selector in PANELS:
            widget = app.query_one(selector)
            region = widget.region
            shown = widget.display
            if shown and region.width == 0:
                print(f"{selector:<14} (hidden at this terminal size)")
                continue
            content = getattr(widget, "flat_content", None)
            if content is None and selector == "#events":
                text = "\n".join(
                    _plain(item.query_one(Label).render())
                    for item in widget.children
                )
            elif content is None and selector == "#controls":
                text = " ".join(
                    f"{s.id}={s.value}" for s in widget.query(Select)
                )
            else:
                text = _plain(content if content is not None else widget.render())
            longest = max((len(line) for line in text.splitlines()), default=0)
            flag = ""
            if shown and longest > region.width:
                flag = f"  !! OVERFLOW ({longest}>{region.width})"
            print(
                f"{selector:<14} x={region.x:<4} y={region.y:<3} "
                f"{region.width}x{region.height:<3} display={shown}{flag}"
            )
            if shown and text.strip():
                for line in text.splitlines()[:14]:
                    print(f"    | {line}")
        if svg_path:
            with open(svg_path, "w", encoding="utf-8") as handle:
                handle.write(app.export_screenshot())
            print(f"\nwrote screenshot: {svg_path}")


def main() -> None:
    """CLI entry for the headless preview."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", default="synthetic")
    parser.add_argument("--size", default="140x46")
    parser.add_argument("--wait", type=float, default=4.0)
    parser.add_argument("--svg", default=None, help="write an SVG screenshot here")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="force the rule fallback (no model calls; fast and deterministic)",
    )
    args = parser.parse_args()
    width, height = (int(part) for part in args.size.lower().split("x"))
    asyncio.run(report(args.feed, width, height, args.wait, args.svg, args.offline))


if __name__ == "__main__":
    main()
