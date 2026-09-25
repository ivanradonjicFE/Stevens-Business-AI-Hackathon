"""Brief writers: one auditable paragraph per audience lens.

Each lens composes a fixed structure from cluster state — severity,
exposed nodes, nearest analog, evidence count. No freeform generation:
every claim traces to a signal or a case file.
"""

from __future__ import annotations

from watchtower.models import EventCluster

CAVEAT = (
    "Model estimate from public-source correlation — verify "
    "independently before acting."
)


def _top_node_line(cluster: EventCluster) -> str:
    if not cluster.exposure:
        return "no monitored supply-chain node currently in range"
    node, score = cluster.exposure[0]
    return f"highest exposure: {node.name} ({node.kind}, score {score:.2f})"


def _top_analog_line(cluster: EventCluster) -> str:
    if not cluster.analogs:
        return "no close historical analog in library"
    analog, score = cluster.analogs[0]
    return f"nearest analog: {analog.name} ({analog.date}, match {score:.2f})"


def health_brief(cluster: EventCluster) -> str:
    """Health lens: who is exposed; strain on health systems."""
    lines = [
        f"[HEALTH] {cluster.title} — {cluster.severity.name}",
        f"Direct health exposure: {_top_node_line(cluster)}.",
    ]
    if cluster.mechanisms & {"casualty", "pandemic", "seismic", "fire"}:
        lines.append(
            "Casualty-capable mechanism detected — expect direct human "
            "impact near the epicenter."
        )
    else:
        lines.append(
            "No direct casualty mechanism; health risk is indirect "
            "(medical device and pharma supply-chain delays)."
        )
    lines.append(
        f"Corroboration: {len(cluster.signals)} signals across "
        f"{len({s.source_type for s in cluster.signals})} source types."
    )
    return "\n".join(lines)


def wealth_brief(cluster: EventCluster) -> str:
    """Wealth lens: markets, sectors, asset classes."""
    lines = [
        f"[WEALTH] {cluster.title} — {cluster.severity.name}",
        f"Exposure: {_top_node_line(cluster)}.",
        f"History: {_top_analog_line(cluster)}.",
    ]
    if cluster.analogs:
        analog, _ = cluster.analogs[0]
        for k, v in list(analog.market_reaction.items())[:3]:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def insurance_brief(cluster: EventCluster) -> str:
    """Insurance lens: lines of business, claims, reserving."""
    lines = [
        f"[INSURANCE] {cluster.title} — {cluster.severity.name}",
        f"Situation: {_top_node_line(cluster)}.",
        f"Precedent: {_top_analog_line(cluster)}.",
    ]
    if cluster.analogs:
        analog, _ = cluster.analogs[0]
        ins = analog.quantified_impact.get("insurance")
        if ins:
            lines.append(f"  {analog.case_id} insurance impact: {ins}")
    lines.append(
        "Lines to watch: marine cargo, contingent business "
        "interruption (CBI), trade credit. Reserving teams should "
        "flag policies with exposure to the affected node list."
    )
    lines.append(f"Caveat: {CAVEAT}")
    return "\n".join(lines)


WRITERS = {
    "health": health_brief,
    "wealth": wealth_brief,
    "insurance": insurance_brief,
}


def write_briefs(cluster: EventCluster) -> EventCluster:
    """Attach all three lens briefs to the cluster."""
    cluster.briefs = {name: fn(cluster) for name, fn in WRITERS.items()}
    return cluster
