"""Central configuration: paths, thresholds, market specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from watchtower.models import Chokepoint

PACKAGE_DIR = Path(__file__).parent
DATA_DIR = PACKAGE_DIR / "data"
REPLAY_DIR = DATA_DIR / "replay"

# --- Correlation ---
CLUSTER_RADIUS_KM = 150.0  # geo radius to join an event cluster
CLUSTER_WINDOW_H = 48.0  # time window for joining signals
MIN_CLUSTER_SIGNALS = 2  # signals needed to form an event
ENTITY_MATCH_BOOST_KM = 500.0  # wider radius if entities overlap

# --- Severity rubric (weights sum to 1.0) ---
W_TYPE = 0.20
W_PROXIMITY = 0.30
W_CORROBORATION = 0.25
W_SPREAD = 0.15
W_ESCALATION = 0.10

SEVERITY_BANDS = [
    (0.75, "SEVERE"),
    (0.55, "HIGH"),
    (0.35, "ELEVATED"),
    (0.18, "GUARDED"),
    (0.00, "LOW"),
]

# Base severity by kind hint (0-1). Unknown kinds score low.
KIND_BASE_SEVERITY = {
    "canal_blockage": 0.85,
    "casualty": 0.7,
    "seismic": 0.75,
    "shipping_anomaly": 0.5,
    "infrastructure": 0.6,
    "extreme_weather": 0.55,
    "advisory": 0.35,
    "geopolitical": 0.5,
    "pandemic": 0.55,
    "labor_disruption": 0.5,
    "materials_shortage": 0.5,
    "drought": 0.5,
    "flood": 0.55,
    "market": 0.3,
    "noise": 0.05,
}

# Map signal kind_hints -> mechanism tags used by the analog library.
# A hint can imply several mechanisms; noise implies none.
HINT_TO_MECHANISMS = {
    "shipping_anomaly": {"shipping_anomaly"},
    "canal_blockage": {"canal_blockage", "chokepoint", "shipping_delay"},
    "infrastructure": {"infrastructure", "chokepoint"},
    "casualty": {"casualty"},
    "market": {"market_reaction"},
    "advisory": {"advisory"},
    "weather": {"extreme_weather"},
    "seismic": {"seismic"},
    "fire": {"fire"},
    "flood": {"flood", "extreme_weather"},
    "drought": {"drought", "extreme_weather"},
    "pandemic": {"pandemic"},
    "labor_disruption": {"labor_disruption"},
    "geopolitical": {"geopolitical"},
    "materials_shortage": {"materials_shortage"},
    "noise": set(),
}

# --- Exposure ---
EXPOSURE_RADIUS_KM = 800.0  # node "affected" radius
EXPOSURE_DECAY_KM = 2000.0  # distance at which effect ~0

# --- Replay ---
REPLAY_SPEED = 120.0  # simulated seconds per real second

# Analogs excluded per scenario so replays can't "see the future" —
# e.g. replaying Suez may not cite the Suez 2021 case file.
SCENARIO_EXCLUSIONS = {
    # Replaying as-of the event date: hide analogs dated during/after it.
    "suez_2021": frozenset({"suez_2021"}),
    "panama_drought_2023": frozenset({"ukraine_neon_2022"}),
    "turkey_syria_earthquake_2023": frozenset({"ukraine_neon_2022"}),
}


@dataclass(frozen=True)
class MarketSpec:
    """A monitored vertical: watchlist + which nodes/mechanisms matter."""

    key: str
    label: str
    index: str
    watchlist: tuple[str, ...]
    node_kinds: frozenset[str]
    mechanisms: frozenset[str]


def load_markets(path: Path | None = None) -> dict[str, MarketSpec]:
    """Load market vertical definitions."""
    raw = yaml.safe_load((path or DATA_DIR / "markets.yaml").read_text())
    return {
        key: MarketSpec(
            key=key,
            label=spec["label"],
            index=spec["index"],
            watchlist=tuple(spec["watchlist"]),
            node_kinds=frozenset(spec["node_kinds"]),
            mechanisms=frozenset(spec["mechanisms"]),
        )
        for key, spec in raw["markets"].items()
    }


def load_chokepoints(path: Path | None = None) -> list[Chokepoint]:
    """Load chokepoint nodes from the YAML graph definition."""
    raw = yaml.safe_load((path or DATA_DIR / "chokepoints.yaml").read_text())
    return [
        Chokepoint(
            node_id=n["id"],
            name=n["name"],
            kind=n["kind"],
            geo=(float(n["geo"][0]), float(n["geo"][1])),
            criticality=float(n["criticality"]),
            notes=n.get("notes", ""),
        )
        for n in raw["nodes"]
    ]
