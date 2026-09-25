"""Analog retriever: which past supply shocks does this rhyme with?

match = 0.55*mechanism_jaccard + 0.30*sector_jaccard + 0.15*geo_proximity

Event "sectors" are derived from the kinds of chokepoint nodes exposed
(fabs -> electronics/autos, waterways -> container shipping, etc.).
Analogs can be excluded for as-of replays so the demo can't "see the
future" (e.g. replaying Suez 2021 cannot cite the Suez 2021 case file).
"""

from __future__ import annotations

from datetime import UTC, datetime

from watchtower.geo import haversine_km
from watchtower.models import Analog, EventCluster

TOP_N = 3
GEO_DECAY_KM = 8000.0

W_MECHANISM = 0.55
W_SECTOR = 0.30
W_GEO = 0.15

# Chokepoint node kind -> sectors it feeds
KIND_TO_SECTORS = {
    "fab": {"electronics", "autos"},
    "osat": {"electronics", "autos"},
    "material": {"electronics"},
    "canal": {"container_shipping", "energy"},
    "strait": {"container_shipping", "energy"},
    "port": {"container_shipping", "retail"},
}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _event_sectors(cluster: EventCluster) -> set[str]:
    sectors: set[str] = set()
    for node, _ in cluster.exposure:
        sectors |= KIND_TO_SECTORS.get(node.kind, set())
    return sectors


def match_score(cluster: EventCluster, analog: Analog) -> float:
    """Weighted similarity between an event and a case file."""
    mech = _jaccard(cluster.mechanisms, set(analog.mechanisms))
    sector = _jaccard(_event_sectors(cluster), set(analog.sectors_hit))
    geo = 0.0
    if cluster.centroid and analog.geo:
        dist = haversine_km(cluster.centroid, analog.geo)
        geo = max(0.0, 1.0 - dist / GEO_DECAY_KM)
    return W_MECHANISM * mech + W_SECTOR * sector + W_GEO * geo


def retrieve_analogs(
    cluster: EventCluster,
    analogs: list[Analog],
    exclude_ids: frozenset[str] = frozenset(),
    top_n: int = TOP_N,
) -> EventCluster:
    """Attach the top-N analog matches to the cluster.

    PIT rule: an analog whose date is after the event's last_seen
    timestamp is future information — always excluded regardless of
    the per-scenario exclusion list.
    """
    as_of_date = datetime.fromtimestamp(cluster.last_seen, UTC).date()
    scored = [
        (a, match_score(cluster, a))
        for a in analogs
        if a.case_id not in exclude_ids and a.date <= as_of_date
    ]
    cluster.analogs = sorted(scored, key=lambda p: p[1], reverse=True)[:top_n]
    return cluster
