"""Situation reports: the artifact a decision-maker actually reads.

Mirrors the layout of the situation-report deliverable shipped with the
project: an index page over all active events (level, region, index,
sites, status), then one page per event with the auditable rubric, the
impact-zone site table, matched historical case files, the
precedent-derived market view, lens briefs, recommended actions, and
the scoring notes.

Everything is derived from pipeline state (severity components, node
exposure, analog matches, raw signals), so every number in a report
traces back to a signal or a case file. Reports are plain markdown, so
they preload cheaply: the dashboard builds each event's report as the
signal arrives and a click just reads the cache.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from watchtower import config
from watchtower.geo import haversine_km
from watchtower.models import EventCluster, Signal

if TYPE_CHECKING:
    from watchtower.agents.council import CouncilReport

DISCLAIMER = (
    "Supply-chain early-warning report - automated correlation of public "
    "signals, not investment or underwriting advice."
)

# Composite index bands (0-100) mirror the severity rubric thresholds.
INDEX_BANDS: tuple[tuple[float, str], ...] = (
    (0.75, "RED"),
    (0.55, "ORANGE"),
    (0.35, "AMBER"),
    (0.18, "YELLOW"),
    (0.00, "GREEN"),
)

# Coarse (region, lat_min, lat_max, lon_min, lon_max) boxes, checked in
# order so overlapping boxes resolve to the more specific label.
REGION_BOXES: tuple[tuple[str, float, float, float, float], ...] = (
    ("taiwan", 21.5, 26.5, 119.0, 123.0),
    ("japan", 30.0, 46.0, 128.0, 146.0),
    ("korea", 33.0, 39.5, 124.0, 130.0),
    ("philippines", 4.0, 21.0, 116.0, 127.0),
    ("southeast_asia", -12.0, 22.0, 92.0, 128.0),
    ("south_asia", 5.0, 32.0, 60.0, 95.0),
    ("middle_east", 12.0, 40.0, 32.0, 63.0),
    ("china", 20.0, 45.0, 97.0, 127.0),
    ("europe", 36.0, 62.0, -12.0, 30.0),
    ("north_america", 15.0, 72.0, -170.0, -50.0),
    ("south_america", -56.0, 15.0, -82.0, -34.0),
    ("africa", -35.0, 35.0, -20.0, 52.0),
    ("oceania", -50.0, -8.0, 110.0, 180.0),
)


def _fmt_ts(ts: float) -> str:
    """Unix seconds -> 'YYYY-MM-DD HH:MM UTC'."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


# Hazard classes for the library-coverage chart: real case files grouped
# by the mechanism vocabulary the correlator emits.
LIBRARY_BUCKETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("weather", frozenset({"extreme_weather", "flood", "drought"})),
    ("seismic", frozenset({"seismic"})),
    ("fire", frozenset({"fire"})),
    ("chokepoint", frozenset({"chokepoint", "canal_blockage", "shipping_delay", "shipping_anomaly"})),
    ("power/fab", frozenset({"power_loss", "fab_shutdown", "infrastructure"})),
    ("labour", frozenset({"labor_disruption"})),
    ("geopolitics", frozenset({"geopolitical"})),
    ("materials", frozenset({"materials_shortage"})),
    ("pandemic", frozenset({"pandemic"})),
)


def library_rows(analogs, top_n: int = 6) -> list[tuple[str, float]]:
    """Real case files per hazard class, for the library chart."""
    rows = []
    for name, mechanisms in LIBRARY_BUCKETS:
        count = sum(1 for analog in analogs if set(analog.mechanisms) & mechanisms)
        if count:
            rows.append((f"{name} ({count})", float(count)))
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows[:top_n]


def region_for(geo: tuple[float, float] | None) -> str:
    """Coarse region label for a (lat, lon) centroid."""
    if geo is None:
        return "unlocated"
    lat, lon = geo
    for name, lat_min, lat_max, lon_min, lon_max in REGION_BOXES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return "global"


def index_band(score: float) -> str:
    """Composite (0-1) -> index band name (RED..GREEN)."""
    for threshold, name in INDEX_BANDS:
        if score >= threshold:
            return name
    return "GREEN"


def index_value(score: float) -> int:
    """Composite (0-1) -> 0-100 index."""
    return round(max(0.0, min(1.0, score)) * 100)


def _severity_rank(cluster: EventCluster) -> int:
    """Ordinal severity (1-5) for report headers."""
    return int(cluster.severity)


def _top_signal(cluster: EventCluster):
    """Highest-credibility signal in the cluster (the lead source)."""
    if not cluster.signals:
        return None
    return max(cluster.signals, key=lambda s: s.credibility)


def _site_rows(cluster: EventCluster, limit: int = 6) -> list[str]:
    """Impact-zone table rows: site, type, distance, score."""
    rows = []
    for node, score in cluster.exposure[:limit]:
        distance = (
            haversine_km(node.geo, cluster.centroid) if cluster.centroid else 0.0
        )
        rows.append(
            f"| {node.name} | {node.kind} | {distance:.0f} km | {score:.2f} |"
        )
    return rows


def _rubric_rows(cluster: EventCluster) -> list[str]:
    """Auditable rubric table rows: component, value, weight, points, why."""
    return [
        f"| {c.name} | {c.value:.2f} | {c.weight:.2f} | "
        f"{c.value * c.weight:.3f} | {c.rationale} |"
        for c in cluster.components
    ]


def _precedent_rows(cluster: EventCluster) -> list[str]:
    """Historical case-file table rows."""
    return [
        f"| {a.name} | {a.date} | {score:.2f} | {a.duration_days}d | "
        f"{', '.join(a.mechanisms[:3])} |"
        for a, score in cluster.analogs
    ]


def _bottom_line(cluster: EventCluster) -> str:
    """One-sentence assessment derived from score, exposure, analogs."""
    band = index_band(cluster.severity_score)
    node = cluster.exposure[0][0].name if cluster.exposure else "no monitored node"
    analog = cluster.analogs[0][0] if cluster.analogs else None
    precedent = (
        f" Closest precedent: {analog.name} ({analog.date}, lasted "
        f"{analog.duration_days}d)."
        if analog
        else " No close precedent in the case library."
    )
    return (
        f"{cluster.title} rates {cluster.severity.name} "
        f"(composite {cluster.severity_score:.2f}, index "
        f"{index_value(cluster.severity_score)}/100 {band}) against a "
        f"{len(cluster.signals)}-signal evidence base. Highest exposure: "
        f"{node}.{precedent} Figures are precedent-derived estimates from "
        f"public sources."
    )


def _what_happened(cluster: EventCluster, limit: int = 3) -> str:
    """Lead-source narrative from the strongest signals."""
    ordered = sorted(cluster.signals, key=lambda s: -s.credibility)[:limit]
    if not ordered:
        return "No signals in cluster."
    return " ".join(
        f"{s.source_name} ({s.source_type}): {s.text.rstrip('.')}." for s in ordered
    )


def _market_view(cluster: EventCluster) -> list[str]:
    """Precedent-derived market view rows + a labelled projection line."""
    if not cluster.analogs:
        return [
            "No historical case file matched, so no precedent-derived view is "
            "issued for this event."
        ]
    analog, score = cluster.analogs[0]
    lines = [
        f"Precedent-derived view from **{analog.name}** "
        f"({analog.date}); match {score:.2f}. The library carries no live "
        "market data, so these are historical reactions, not forecasts.",
        "",
        "| signal | historical reaction |",
        "|---|---|",
    ]
    for key, value in analog.market_reaction.items():
        lines.append(f"| {key} | {value} |")
    for key, value in list(analog.quantified_impact.items())[:3]:
        lines.append(f"| {key} | {value} |")
    return lines


# Forecast signals identify themselves in their own text (see the weather
# collectors in ``watchtower.sources``), which keeps the ``Signal`` schema - and
# therefore every replay file - unchanged.
_FORECAST_MARK = "forecast:"
_LEAD_RE = re.compile(r"\(\+(\d+)d\)")


def is_forecast(signal: Signal) -> bool:
    """Is this a projection rather than an observation?"""
    return _FORECAST_MARK in signal.text.lower()


def forecast_lead_days(signal: Signal) -> int | None:
    """Lead time in days a forecast states in its own text, else ``None``."""
    match = _LEAD_RE.search(signal.text)
    return int(match.group(1)) if match else None


def forecast_claim(signal: Signal) -> str:
    """The model's claim, without the trailing nearest-node annotation."""
    return signal.text.split("(nearest supply node")[0].strip()


def rank_forecasts(
    origin: tuple[float, float] | None,
    signals: Iterable[Signal],
    limit: int = 12,
) -> list[Signal]:
    """Forecasts for a report about ``origin``: nearest first, then soonest.

    A per-node forecast rarely forms an event on its own (the correlator wants
    two signals), so callers rank the whole forecast lane by distance to the
    event rather than scoping to that event's own signals. With no origin to
    rank against, the soonest-issued forecasts lead.
    """
    forecasts = [s for s in signals if is_forecast(s)]
    if not forecasts:
        return []
    if origin is None:
        return sorted(forecasts, key=lambda s: s.ts)[:limit]

    def key(signal: Signal) -> tuple[int, float, float]:
        if signal.geo:
            return (0, haversine_km(origin, signal.geo), signal.ts)
        return (1, 0.0, signal.ts)

    return sorted(forecasts, key=key)[:limit]


def early_indicator_rows(signals: Iterable[Signal]) -> list[str]:
    """Markdown table of the forecasts behind an event, soonest first.

    These are the leading indicators: what the models say is coming, with the
    lead time they said it at. Kept separate from the observed-event scoring,
    because a 3-day-out projection should not be read as something that
    happened.
    """
    forecasts = sorted((s for s in signals if is_forecast(s)), key=lambda s: s.ts)
    if not forecasts:
        return []
    rows = [
        "| what the models say | lead time | source | credibility |",
        "|---|---|---|---|",
    ]
    for signal in forecasts:
        claim = forecast_claim(signal).replace("|", "/")  # never break the table
        lead = forecast_lead_days(signal)
        rows.append(
            f"| {claim} | {f'+{lead}d' if lead is not None else 'now'} | "
            f"{signal.source_name} | {signal.credibility:.2f} |"
        )
    return rows


def council_rows(report: CouncilReport | None) -> list[str]:
    """Markdown block for the AI council's read, provenance included.

    The lexical relevance row is labelled deterministic on purpose: it comes
    from :mod:`watchtower.relevance`, not from the model, so a reader can
    recompute it from the market config. ``Origin`` says whether the read
    came from a model or from the rule fallback.
    """
    if report is None:
        return [
            "No council brief for this event yet (the council analyses new or "
            "escalating events only)."
        ]
    origin = "model-assisted" if report.model_used else "rule fallback (no model reachable)"
    rows = [
        "| field | value |",
        "|---|---|",
        f"| Council level | {report.level} - "
        f"{'notifiable' if report.notifiable else 'monitoring only'} |",
        f"| Trigger | {report.trigger} |",
        f"| Origin | {origin} |",
        f"| Admitted via | {report.relevance_route} route |",
        f"| Lexical relevance | {report.lexical:.2f} of 1 (deterministic, "
        "computed from the market config) |",
        f"| Context tokens hit | {', '.join(report.lexical_matched) or 'none'} |",
        "",
        f"**Assessment.** {report.headline}",
        "",
        f"**Research brief.** {report.research}",
        "",
        f"**Market view.** {report.market}",
        "",
        f"**Critique.** {report.critique}",
    ]
    if report.segments:
        rows += ["", "| segment | tickers | rationale |", "|---|---|---|"]
        rows += [f"| {name} | {tk} | {why} |" for name, tk, why in report.segments]
    return rows


def event_report(
    cluster: EventCluster,
    *,
    market_label: str = "Semiconductor",
    market_index: str = "SOX",
    watchlist: tuple[str, ...] = (),
    as_of_ts: float | None = None,
    notified: bool = False,
    council_report: CouncilReport | None = None,
) -> str:
    """Render one event's page as markdown."""
    cutoff = _fmt_ts(as_of_ts if as_of_ts is not None else cluster.last_seen)
    index = index_value(cluster.severity_score)
    band = index_band(cluster.severity_score)
    lead = _top_signal(cluster)
    sources = {s.source_type for s in cluster.signals}
    lead_line = (
        f"{lead.source_name} ({lead.source_type}, credibility "
        f"{lead.credibility:.2f})"
        if lead
        else "-"
    )
    status = "NOTIFIED (alert emitted)" if notified else "monitoring only"

    parts: list[str] = [
        f"# [{cluster.severity.name}] {cluster.title} - composite "
        f"{cluster.severity_score:.2f}",
        f"*Situation report - information cutoff {cutoff}*",
        "",
        "| field | value |",
        "|---|---|",
        f"| Level | {cluster.severity.name} ({_severity_rank(cluster)}/5) |",
        f"| Composite index | {index} / 100 - {band} |",
        f"| Region | {region_for(cluster.centroid)} |",
        f"| Market lens | {market_label} ({market_index}) |",
        f"| Signals | {len(cluster.signals)} across {len(sources)} source types |",
        f"| Lead source | {lead_line} |",
        f"| Exposure | {cluster.exposure[0][1]:.2f} - "
        f"{cluster.exposure[0][0].name} |"
        if cluster.exposure
        else "| Exposure | no monitored node in range |",
        f"| Status | {status} |",
        "",
        "**Bottom line.** " + _bottom_line(cluster),
        "",
        "**What happened.** " + _what_happened(cluster),
    ]
    # Forecasts lead the page: they are the only part of this report that is
    # about what has not happened yet, and they are the reason to act early.
    early = early_indicator_rows(cluster.signals)
    if early:
        parts += ["", "## Early indicators (forecasts)", *early]
    parts += [
        "",
        "## Score breakdown (auditable rubric)",
        "| component | value | weight | points | rationale |",
        "|---|---|---|---|---|",
        *_rubric_rows(cluster),
        "",
        f"*Composite = sum(points) = {cluster.severity_score:.3f}. Bands: "
        + " - ".join(
            f"{name} >= {threshold:.2f}"
            for threshold, name in reversed(config.SEVERITY_BANDS)
        )
        + "*",
        "",
        "## Sites in the impact zone",
    ]
    if cluster.exposure:
        parts += [
            "| site | type | distance | score |",
            "|---|---|---|---|",
            *_site_rows(cluster),
        ]
    else:
        parts.append("No monitored supply-chain node within exposure range.")

    parts += ["", "## Historical precedents"]
    if cluster.analogs:
        parts += [
            "| case file | date | match | duration | mechanisms |",
            "|---|---|---|---|---|",
            *_precedent_rows(cluster),
        ]
    else:
        parts.append("No case file matched this mechanism/sector/geo profile.")

    parts += ["", "## Precedent-derived market view", *_market_view(cluster)]

    parts += ["", "## AI council read", *council_rows(council_report)]

    if watchlist:
        node = cluster.exposure[0][0] if cluster.exposure else None
        why = (
            f"watch for confirmed impact on {node.name} ({node.kind})"
            if node
            else "no confirmed node impact yet"
        )
        parts += [
            "",
            "## Watchlist to monitor",
            f"{market_label} ({market_index}): "
            + ", ".join(watchlist)
            + f" - {why}.",
        ]

    parts += [
        "",
        "## Lens briefs",
        "",
        "**Health**",
        cluster.briefs.get("health", "-"),
        "",
        "**Wealth**",
        cluster.briefs.get("wealth", "-"),
        "",
        "**Insurance**",
        cluster.briefs.get("insurance", "-"),
        "",
        "## Recommended actions",
        "- **Monitor**: keep tracking the lead source(s) for movement toward a "
        "monitored node.",
        "- **Verify**: require a company, port, utility, or government notice "
        "before treating a node as affected.",
        "- **Attribution**: do not attribute market moves to this event without "
        "confirmed site impacts.",
        "",
        "## How this was scored",
        f"- {len(cluster.signals)} signals from {len(sources)} source types "
        f"({', '.join(sorted(sources))}).",
        "- Events join a cluster on shared entities or within "
        f"{config.CLUSTER_RADIUS_KM:.0f} km / {config.CLUSTER_WINDOW_H:.0f} h; "
        f"clusters need >= {config.MIN_CLUSTER_SIGNALS} signals to surface.",
        "- Exposure = node criticality x distance decay "
        f"({config.EXPOSURE_DECAY_KM:.0f} km); analog match = mechanism + "
        "sector + geography similarity.",
        "",
        "## Evidence trail",
        "| time | source | signal |",
        "|---|---|---|",
        *[
            f"| {_fmt_ts(s.ts)} | {s.source_name} ({s.source_type}) | "
            f"{s.text} |"
            for s in cluster.signals
        ],
        "",
        f"*{DISCLAIMER}*",
    ]
    return "\n".join(parts)


def situation_report(
    clusters: list[EventCluster],
    *,
    market_label: str = "Semiconductor",
    market_index: str = "SOX",
    as_of_ts: float | None = None,
    notified_ids: frozenset[str] = frozenset(),
    include_details: bool = True,
) -> str:
    """Index page over all active events, optionally with every detail page."""
    cutoff = _fmt_ts(as_of_ts or (clusters[0].last_seen if clusters else 0.0))
    parts: list[str] = [
        f"# {market_label} Supply-Chain Situation Report",
        f"*Report type: point-in-time (PIT) - {cutoff}. Reflects only signals "
        f"received on or before the cutoff.*",
        "",
        f"{len(clusters)} active event(s). Level from the auditable severity "
        "rubric; index is the composite score scaled to 0-100.",
        "",
        "| level | event | region | index | sites | signals | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for cluster in clusters:
        parts.append(
            f"| {cluster.severity.name} | {cluster.title} | "
            f"{region_for(cluster.centroid)} | "
            f"{index_value(cluster.severity_score)} "
            f"{index_band(cluster.severity_score)} | "
            f"{len(cluster.exposure)} | {len(cluster.signals)} | "
            f"{'notified' if cluster.event_id in notified_ids else '-'} |"
        )
    if not clusters:
        parts.append("| - | no active events | - | - | - | - | - |")

    if include_details:
        for cluster in clusters:
            parts += [
                "",
                "---",
                "",
                event_report(
                    cluster,
                    market_label=market_label,
                    market_index=market_index,
                    as_of_ts=as_of_ts,
                    notified=cluster.event_id in notified_ids,
                ),
            ]
    parts += ["", f"*{DISCLAIMER}*"]
    return "\n".join(parts)


@dataclass
class ReportCache:
    """Preloaded reports: build on ingest, read instantly on click.

    The dashboard calls :meth:`preload` for the cluster that just changed
    and :meth:`preload_index` when the active set shifts, so opening a
    report never does work in the click handler.
    """

    market_label: str = "Semiconductor"
    market_index: str = "SOX"
    watchlist: tuple[str, ...] = ()
    pages: dict[str, str] = field(default_factory=dict)
    index: str = ""
    _signature: tuple[tuple[str, float, int], ...] = ()

    def preload(
        self,
        cluster: EventCluster,
        *,
        as_of_ts: float | None = None,
        notified: bool = False,
        council: CouncilReport | None = None,
    ) -> str:
        """Build and cache one event's report; returns the markdown."""
        page = event_report(
            cluster,
            market_label=self.market_label,
            market_index=self.market_index,
            watchlist=self.watchlist,
            as_of_ts=as_of_ts,
            notified=notified,
            council_report=council,
        )
        self.pages[cluster.event_id] = page
        return page

    def preload_index(
        self,
        clusters: list[EventCluster],
        *,
        as_of_ts: float | None = None,
        notified_ids: frozenset[str] = frozenset(),
        force: bool = False,
    ) -> str:
        """Rebuild the index page when the active set or levels change."""
        signature = tuple(
            (c.event_id, round(c.severity_score, 3), len(c.signals))
            for c in clusters
        )
        if not force and signature == self._signature and self.index:
            return self.index
        self.index = situation_report(
            clusters,
            market_label=self.market_label,
            market_index=self.market_index,
            as_of_ts=as_of_ts,
            notified_ids=notified_ids,
            include_details=False,
        )
        self._signature = signature
        return self.index

    def get(self, event_id: str) -> str | None:
        """Cached report page for an event, if preloaded."""
        return self.pages.get(event_id)

    def preloaded(self) -> int:
        """How many event pages are ready to display."""
        return len(self.pages)
