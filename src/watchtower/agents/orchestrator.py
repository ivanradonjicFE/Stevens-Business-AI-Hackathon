"""Orchestrator: drives signal -> assessment -> alert flow.

One instance per market vertical. On each signal: correlate, expose,
score, match analogs, write briefs. Emits an Alert on each severity
upgrade at or above ALERT_THRESHOLD.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from watchtower import config
from watchtower.agents.analog import retrieve_analogs
from watchtower.agents.briefs import write_briefs
from watchtower.agents.correlator import Correlator
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
        self._maybe_alert(cluster)
        return cluster

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

    def _maybe_alert(self, cluster: EventCluster) -> None:
        """Emit an alert on each new severity-band upgrade."""
        band = cluster.severity
        prev = self._last_band.get(cluster.event_id, Severity.LOW)
        if band > prev and band >= ALERT_THRESHOLD:
            self._last_band[cluster.event_id] = band
            alert = Alert(
                event=cluster,
                issued_at=cluster.last_seen,
                headline=(
                    f"{band.name}: {cluster.title} — "
                    f"{len(cluster.signals)} corroborating signals"
                ),
                severity=band,
            )
            self.alerts.append(alert)
            log.info("ALERT %s", alert.headline)
            if self.on_alert:
                self.on_alert(alert)
