"""Orchestrator: drives signal -> assessment -> alert flow.

One instance per market vertical. On each signal: correlate, expose,
score, match analogs, write briefs, then hand new or escalating clusters
to the AI council. Emits an Alert on each severity upgrade at or above
ALERT_THRESHOLD; when the council has already finished a brief for that
event (the rule path, i.e. no model reachable) its level and lexical
relevance are attached to the alert.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from watchtower import config
from watchtower.agents.analog import retrieve_analogs
from watchtower.agents.briefs import write_briefs
from watchtower.agents.correlator import Correlator
from watchtower.agents.council import Council, CouncilReport
from watchtower.agents.exposure import assess_exposure
from watchtower.agents.severity import score_cluster
from watchtower.config import MarketSpec
from watchtower.data.analogs._schema import load_analogs
from watchtower.models import Alert, EventCluster, Severity, Signal

log = logging.getLogger(__name__)

ALERT_THRESHOLD = Severity.ELEVATED


class Orchestrator:
    """Per-market pipeline: stateful over the ingest stream."""

    def __init__(
        self,
        market: MarketSpec,
        data_dir: Path | None = None,
        exclude_analogs: frozenset[str] = frozenset(),
        on_alert: Callable[[Alert], None] | None = None,
        enable_council: bool = True,
        council: Council | None = None,
    ) -> None:
        self.market = market
        self.chokepoints = [
            n for n in config.load_chokepoints() if n.kind in market.node_kinds
        ]
        self.analogs = load_analogs(data_dir)
        self.exclude_analogs = exclude_analogs
        self.correlator = Correlator()
        self.on_alert = on_alert
        self.alerts: list[Alert] = []
        # The council is the AI layer. With no key it runs its rule fallback
        # inline, so the dashboard always has a read to show; with a key it
        # dispatches to a thread pool and briefs land a moment later.
        self.council: Council | None = (
            council if council is not None else (Council(market) if enable_council else None)
        )
        self._last_band: dict[str, Severity] = {}

    def process(self, signal: Signal) -> EventCluster:
        """Run one signal through the full agent chain."""
        cluster = self.correlator.ingest(signal)
        if len(cluster.signals) < config.MIN_CLUSTER_SIGNALS:
            return cluster
        assess_exposure(cluster, self.chokepoints)
        score_cluster(cluster, self.chokepoints)
        retrieve_analogs(cluster, self.analogs, self.exclude_analogs)
        write_briefs(cluster)
        report = self.council.consider(cluster) if self.council else None
        self._maybe_alert(cluster, report)
        return cluster

    def council_report(self, event_id: str) -> CouncilReport | None:
        """The council's read for one event, if it has produced one yet."""
        return self.council.report_for(event_id) if self.council else None

    def council_notifications(self) -> list[CouncilReport]:
        """Council briefs at or above the notify tier (WATCH/WARNING)."""
        return self.council.notifications() if self.council else []

    def close(self) -> None:
        """Release the council's worker pool (idempotent).

        A live council runs its model calls on a thread pool whose threads are
        joined at interpreter exit, so without this a quit would appear to hang
        while in-flight calls finish.
        """
        if self.council is not None:
            self.council.close()

    def chokepoint_status(self) -> list[tuple[str, float]]:
        """Per-node worst exposure across active events, descending.

        The live equivalent of WorldMonitor's chokepoint board: each
        node's score is its strongest current exposure signal.
        """
        worst: dict[str, float] = {}
        for cluster in self.correlator.active_events():
            for node, score in cluster.exposure:
                worst[node.name] = max(worst.get(node.name, 0.0), score)
        return sorted(worst.items(), key=lambda p: p[1], reverse=True)

    def _maybe_alert(
        self, cluster: EventCluster, report: CouncilReport | None = None
    ) -> None:
        """Emit an alert on each new severity-band upgrade.

        ``report`` is the council's brief when the rule path ran inline; the
        alert then carries the council's own level and its lexical relevance,
        so a notification is traceable to the AI read that produced it.
        """
        band = cluster.severity
        prev = self._last_band.get(cluster.event_id, Severity.LOW)
        if band > prev and band >= ALERT_THRESHOLD:
            self._last_band[cluster.event_id] = band
            headline = (
                f"{band.name}: {cluster.title} — "
                f"{len(cluster.signals)} corroborating signals"
            )
            extra: dict = {}
            if report is not None:
                headline += f" | council {report.level}"
                extra["caveat"] = (
                    f"AI council read: {report.level} via the "
                    f"{report.relevance_route} route, lexical relevance "
                    f"{report.lexical:.2f} ({report.trigger}). Early-warning "
                    "assessment from public-source correlation; figures are "
                    "estimates - verify before acting."
                )
            alert = Alert(
                event=cluster,
                issued_at=cluster.last_seen,
                headline=headline,
                severity=band,
                **extra,
            )
            self.alerts.append(alert)
            log.info("ALERT %s", alert.headline)
            if self.on_alert:
                self.on_alert(alert)
