"""Watchtower: semiconductor supply-shock early-warning system.

Multi-agent research pipeline: correlates weak signals across sources,
scores severity with an auditable rubric, matches historical analogs,
and writes Health/Wealth/Insurance impact briefs.
"""


def main() -> None:
    """Console script entry point — launches the TUI."""
    from watchtower.ui.app import main as app_main

    app_main()
