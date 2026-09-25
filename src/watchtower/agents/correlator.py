"""Correlator: clusters weak signals into candidate events.

A signal joins an existing cluster when it shares entities, or falls
within the geo radius and time window of that cluster. Clusters with
fewer than MIN_CLUSTER_SIGNALS stay pending and don't surface.
"""

from __future__ import annotations

from watchtower import config
from watchtower.geo import haversine_km
from watchtower.models import EventCluster, Signal


def _entities_overlap(a: Signal, cluster: EventCluster) -> bool:
    cluster_entities = {e for s in cluster.signals for e in s.entities}
    return bool(set(a.entities) & cluster_entities)


def _in_window(sig: Signal, cluster: EventCluster) -> bool:
    window_s = config.CLUSTER_WINDOW_H * 3600
    return abs(sig.ts - cluster.last_seen) <= window_s


def _in_radius(sig: Signal, cluster: EventCluster) -> bool:
    if sig.geo is None or cluster.centroid is None:
        return False
    radius = config.CLUSTER_RADIUS_KM
    if _entities_overlap(sig, cluster):
        radius = config.ENTITY_MATCH_BOOST_KM
    return haversine_km(sig.geo, cluster.centroid) <= radius


def _recenter(cluster: EventCluster) -> None:
    """Credibility-weighted centroid over geo-tagged signals."""
    tagged = [(s.geo, s.credibility) for s in cluster.signals if s.geo]
    if not tagged:
        cluster.centroid = None
        return
    total = sum(w for _, w in tagged) or 1.0
    lat = sum(g[0] * w for g, w in tagged) / total
    lon = sum(g[1] * w for g, w in tagged) / total
    cluster.centroid = (lat, lon)


def _derive_title(cluster: EventCluster) -> str:
    """Title from the most common entity, falling back to kind hint."""
    freq: dict[str, int] = {}
    for s in cluster.signals:
        for e in s.entities:
            freq[e] = freq.get(e, 0) + 1
    if freq:
        top = max(freq, key=lambda k: freq[k])
        return top.replace("_", " ").title()
    if cluster.mechanisms:
        return sorted(cluster.mechanisms)[0].replace("_", " ").title()
    return "Unclassified event"


class Correlator:
    """Stateful signal->cluster assignment over the ingest stream."""

    def __init__(self) -> None:
        self.clusters: dict[str, EventCluster] = {}
        self._counter = 0

    def ingest(self, signal: Signal) -> EventCluster:
        """Route a signal into a cluster, creating one if needed."""
        best = self._best_match(signal)
        if best is None:
            self._counter += 1
            best = EventCluster(
                event_id=f"EVT-{self._counter:03d}",
                title="",
                first_seen=signal.ts,
                last_seen=signal.ts,
            )
            self.clusters[best.event_id] = best
        best.signals.append(signal)
        best.last_seen = max(best.last_seen, signal.ts)
        best.first_seen = min(best.first_seen, signal.ts)
        if signal.kind_hint:
            best.mechanisms |= config.HINT_TO_MECHANISMS.get(
                signal.kind_hint, {signal.kind_hint}
            )
        _recenter(best)
        best.title = _derive_title(best)
        return best

    def _best_match(self, signal: Signal) -> EventCluster | None:
        for cluster in self.clusters.values():
            if not _in_window(signal, cluster):
                continue
            if _entities_overlap(signal, cluster) or _in_radius(signal, cluster):
                return cluster
        return None

    def active_events(self) -> list[EventCluster]:
        """Clusters large enough to surface, newest activity first."""
        return sorted(
            (
                c
                for c in self.clusters.values()
                if len(c.signals) >= config.MIN_CLUSTER_SIGNALS
            ),
            key=lambda c: c.last_seen,
            reverse=True,
        )
