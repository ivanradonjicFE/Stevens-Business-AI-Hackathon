"""Core data types for the Watchtower early-warning pipeline.

Every agent consumes and produces these types. Scores carry their
rationale and evidence so every downstream number is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    """Ordered severity bands, increasing urgency."""

    LOW = 1
    GUARDED = 2
    ELEVATED = 3
    HIGH = 4
    SEVERE = 5


@dataclass(frozen=True)
class Signal:
    """One raw observation from a monitored source.

    Signals are intentionally weak: an AIS anomaly, a tweet, a wire
    line. Correlation across signals is what creates an event.
    """

    ts: float  # unix timestamp (UTC)
    source_type: str  # vessel_tracking|wire|social|...
    source_name: str
    url: str
    text: str
    geo: tuple[float, float] | None = None  # (lat, lon) of subject
    entities: tuple[str, ...] = ()
    lang: str = "en"
    kind_hint: str = ""  # shipping_anomaly|casualty|market|...
    credibility: float = 0.5  # 0-1, source reliability prior
    signal_id: str = ""  # stable id; filled on ingest


@dataclass
class Chokepoint:
    """A node in the semiconductor supply chain map."""

    node_id: str
    name: str
    kind: str  # fab|canal|port|material|osat|strait
    geo: tuple[float, float]
    criticality: float  # 0-1, share-of-supply proxy
    notes: str = ""


@dataclass
class Analog:
    """A historical supply-shock case file."""

    case_id: str
    name: str
    date: str
    duration_days: int
    mechanisms: tuple[str, ...]
    sectors_hit: tuple[str, ...]
    summary: str
    market_reaction: dict[str, str]
    quantified_impact: dict[str, str]
    lessons: str
    geo: tuple[float, float] | None = None
    region: str = ""
    sources: tuple[dict[str, str], ...] = ()


@dataclass
class ScoreComponent:
    """One line of the auditable severity breakdown."""

    name: str
    value: float  # 0-1 contribution
    weight: float
    rationale: str


@dataclass
class EventCluster:
    """Correlated signals treated as a single candidate event."""

    event_id: str
    title: str
    signals: list[Signal] = field(default_factory=list)
    centroid: tuple[float, float] | None = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    mechanisms: set[str] = field(default_factory=set)

    # Filled by downstream agents
    severity: Severity = Severity.LOW
    severity_score: float = 0.0  # 0-1 composite
    components: list[ScoreComponent] = field(default_factory=list)
    exposure: list[tuple[Chokepoint, float]] = field(default_factory=list)
    analogs: list[tuple[Analog, float]] = field(default_factory=list)
    briefs: dict[str, str] = field(default_factory=dict)

    def evidence_ids(self) -> list[str]:
        """Signal IDs backing this event, for audit trails."""
        return [s.signal_id for s in self.signals]


@dataclass
class Alert:
    """A decision-maker-ready notification for one event."""

    event: EventCluster
    issued_at: float
    headline: str
    severity: Severity
    caveat: str = (
        "Early-warning assessment from public-source correlation. "
        "Figures are model estimates; verify before acting."
    )
