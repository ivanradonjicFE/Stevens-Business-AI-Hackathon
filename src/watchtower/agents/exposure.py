"""Exposure matcher: how close is the event to the supply chain?

exposure(node) = node.criticality * max(0, 1 - dist/decay_radius)

Pure distance decay — simple, explainable, auditable. Only nodes with
material exposure are surfaced.
"""

from __future__ import annotations

import logging

from watchtower import config
from watchtower.geo import haversine_km
from watchtower.models import Chokepoint, EventCluster

log = logging.getLogger(__name__)

MIN_EXPOSURE = 0.15
TOP_N = 6


def node_exposure(node: Chokepoint, centroid: tuple[float, float]) -> float:
    """Distance-decayed exposure of one node to an event."""
    dist = haversine_km(node.geo, centroid)
    return node.criticality * max(0.0, 1.0 - dist / config.EXPOSURE_DECAY_KM)


def assess_exposure(
    cluster: EventCluster, chokepoints: list[Chokepoint]
) -> EventCluster:
    """Attach ranked (node, exposure) pairs to the cluster."""
    if cluster.centroid is None:
        cluster.exposure = []
        return cluster
    scored = [(n, node_exposure(n, cluster.centroid)) for n in chokepoints]
    cluster.exposure = sorted(
        (p for p in scored if p[1] >= MIN_EXPOSURE),
        key=lambda p: p[1],
        reverse=True,
    )[:TOP_N]
    if not cluster.exposure:
        # debug, not warning: peripheral noise clusters legitimately sit far
        # from every monitored node, and warnings would spray stderr over
        # the TUI.
        log.debug(
            "cluster %s: no chokepoints within exposure range",
            cluster.event_id,
        )
    return cluster
