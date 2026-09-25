"""Severity scorer: transparent rubric, every point explained.

score = W_TYPE*type + W_PROXIMITY*proximity + W_CORROB*corroboration
      + W_SPREAD*spread + W_ESC*escalation

Each component writes a ScoreComponent with a rationale so the final
band is auditable line-by-line.
"""

from __future__ import annotations

from watchtower import config
from watchtower.models import Chokepoint, EventCluster, ScoreComponent, Severity


def _type_component(cluster: EventCluster) -> ScoreComponent:
    hits = {
        h: config.KIND_BASE_SEVERITY.get(h, 0.0)
        for s in cluster.signals
        for h in {s.kind_hint}
        if h
    }
    value = max(hits.values(), default=0.0)
    top = max(hits, key=lambda k: hits[k], default="none")
    return ScoreComponent(
        "event_type",
        value,
        config.W_TYPE,
        f"highest-severity kind seen: {top} ({value:.2f})",
    )


def _proximity_component(
    cluster: EventCluster, chokepoints: list[Chokepoint]
) -> ScoreComponent:
    nearest = max(cluster.exposure, key=lambda e: e[1], default=None)
    if nearest is None:
        return ScoreComponent(
            "proximity",
            0.0,
            config.W_PROXIMITY,
            "no supply-chain node within range",
        )
    node, score = nearest
    return ScoreComponent(
        "proximity",
        score,
        config.W_PROXIMITY,
        f"{node.name} exposure {score:.2f} (criticality {node.criticality:.2f})",
    )


def _corroboration_component(cluster: EventCluster) -> ScoreComponent:
    types = {s.source_type for s in cluster.signals}
    cred = (
        sum(s.credibility for s in cluster.signals) / len(cluster.signals)
        if cluster.signals
        else 0.0
    )
    breadth = min(1.0, len(types) / 4)
    value = breadth * (0.4 + 0.6 * cred)
    return ScoreComponent(
        "corroboration",
        value,
        config.W_CORROBORATION,
        f"{len(types)} source types, mean credibility {cred:.2f}",
    )


def _spread_component(cluster: EventCluster) -> ScoreComponent:
    cells = {
        (round(s.geo[0] / 10), round(s.geo[1] / 10)) for s in cluster.signals if s.geo
    }
    value = min(1.0, len(cells) / 3)
    return ScoreComponent(
        "spread",
        value,
        config.W_SPREAD,
        f"{len(cells)} distinct regions reporting",
    )


def _escalation_component(cluster: EventCluster) -> ScoreComponent:
    recent = sum(1 for s in cluster.signals if cluster.last_seen - s.ts <= 6 * 3600)
    value = min(1.0, recent / 6)
    return ScoreComponent(
        "escalation",
        value,
        config.W_ESCALATION,
        f"{recent} signals in trailing 6h",
    )


def band_for(score: float) -> Severity:
    """Map a composite score to a named severity band."""
    for threshold, name in config.SEVERITY_BANDS:
        if score >= threshold:
            return Severity[name]
    return Severity.LOW


def score_cluster(cluster: EventCluster, chokepoints: list[Chokepoint]) -> EventCluster:
    """Compute and attach the auditable severity breakdown."""
    cluster.components = [
        _type_component(cluster),
        _proximity_component(cluster, chokepoints),
        _corroboration_component(cluster),
        _spread_component(cluster),
        _escalation_component(cluster),
    ]
    cluster.severity_score = sum(c.value * c.weight for c in cluster.components)
    cluster.severity = band_for(cluster.severity_score)
    return cluster
