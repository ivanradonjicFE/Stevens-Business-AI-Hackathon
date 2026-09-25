"""Analog case file schema and loader."""

from __future__ import annotations

from pathlib import Path

import yaml

from watchtower.models import Analog

ANALOG_DIR = Path(__file__).parent


def load_analogs(directory: Path | None = None) -> list[Analog]:
    """Load every analog case file from the data directory."""
    root = directory or ANALOG_DIR
    analogs: list[Analog] = []
    for path in sorted(root.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        analogs.append(
            Analog(
                case_id=raw["id"],
                name=raw["name"],
                date=raw["date"],
                duration_days=int(raw["duration_days"]),
                geo=tuple(raw["region"]) if raw.get("region") else None,
                mechanisms=tuple(raw["mechanisms"]),
                sectors_hit=tuple(raw.get("sectors_hit", ())),
                summary=raw["summary"].strip(),
                market_reaction=dict(raw.get("market_reaction", {})),
                quantified_impact=dict(raw.get("quantified_impact", {})),
                lessons=raw.get("lessons", "").strip(),
                region=raw.get("report_region", ""),
                sources=tuple(raw.get("sources", ())),
            )
        )
    return analogs
