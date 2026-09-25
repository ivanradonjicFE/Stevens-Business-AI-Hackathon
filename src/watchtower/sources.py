"""Signal ingestion: replay files and live GDELT news.

Signals arrive as one JSON object per line (see
tasks/teammate-signals.md for the schema). Timestamps are ISO8601 UTC
on the wire and unix seconds internally.

Live mode pulls point-in-time news from GDELT's DOC 2.1 API: the query
window's `end` is the information cutoff — no article newer than `end`
is ever returned.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from watchtower.models import Signal

log = logging.getLogger(__name__)


def _signal_id(ts: float, source_name: str, text: str) -> str:
    """Stable content-derived id so replays are reproducible."""
    digest = hashlib.sha256(f"{ts}{source_name}{text}".encode())
    return digest.hexdigest()[:10]


def _parse_ts(raw: str) -> float:
    """ISO8601 UTC string -> unix seconds."""
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def parse_signal(raw: dict) -> Signal:
    """Validate and convert one JSON record into a Signal."""
    ts = _parse_ts(raw["ts"])
    geo = raw.get("geo")
    return Signal(
        ts=ts,
        source_type=raw.get("source_type", "unknown"),
        source_name=raw.get("source_name", "unknown"),
        url=raw.get("url", ""),
        text=raw["text"],
        geo=(float(geo[0]), float(geo[1])) if geo else None,
        entities=tuple(e.lower() for e in raw.get("entities", ())),
        lang=raw.get("lang", "en"),
        kind_hint=raw.get("kind_hint", ""),
        credibility=float(raw.get("credibility", 0.5)),
        signal_id=_signal_id(ts, raw.get("source_name", ""), raw["text"]),
    )


def replay_signals(path: Path) -> Iterator[Signal]:
    """Yield signals from a JSONL replay file in timestamp order."""
    if not path.exists():
        raise FileNotFoundError(f"replay file missing: {path}")
    signals = []
    for lineno, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            signals.append(parse_signal(json.loads(line)))
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("skipping bad signal %s:%d: %s", path, lineno, exc)
    if not signals:
        log.warning("replay file produced zero signals: %s", path)
    yield from sorted(signals, key=lambda s: s.ts)


GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Minimal gazetteer: place-name -> (lat, lon, entity tokens) so live
# headlines (which carry no geodata) can still cluster by geography.
_GAZETTEER = {
    "ukraine": (48.4, 31.2, ("ukraine",)),
    "odesa": (46.48, 30.72, ("ukraine", "odesa")),
    "odessa": (46.48, 30.72, ("ukraine", "odesa")),
    "kyiv": (50.45, 30.52, ("ukraine", "kyiv")),
    "black sea": (43.4, 34.3, ("black sea",)),
    "suez": (30.02, 32.55, ("suez canal",)),
    "red sea": (20.0, 38.5, ("red sea",)),
    "bab el-mandeb": (12.58, 43.33, ("bab el-mandeb", "red sea")),
    "houthi": (13.5, 44.0, ("houthi", "red sea")),
    "shanghai": (31.23, 121.49, ("shanghai",)),
    "panama": (9.08, -79.68, ("panama canal",)),
    "baltimore": (39.29, -76.61, ("baltimore",)),
    "nord stream": (55.3, 15.5, ("nord stream",)),
    "baltic": (55.5, 17.0, ("baltic sea",)),
    "taiwan": (23.7, 121.0, ("taiwan",)),
    "detroit": (42.33, -83.05, ("detroit",)),
    "turkey": (38.9, 35.2, ("turkey",)),
    "hormuz": (26.57, 56.25, ("strait of hormuz",)),
    # --- weather-scope additions ---
    "florida": (28.0, -82.5, ("florida",)),
    "georgia": (32.6, -83.4, ("georgia",)),
    "north carolina": (35.5, -79.5, ("north carolina",)),
    "texas": (31.0, -97.5, ("texas",)),
    "hurricane": (25.0, -80.0, ("hurricane",)),
    "flood": (0.0, 0.0, ("flood",)),
    "wildfire": (34.0, -118.0, ("wildfire",)),
    "helene": (28.0, -84.0, ("hurricane helene", "florida")),
    "milton": (27.5, -83.0, ("hurricane milton", "florida")),
    "storm": (25.0, -80.0, ("storm",)),
    "tornado": (36.0, -95.0, ("tornado",)),
    "drought": (9.0, -80.0, ("drought",)),
    "sandstorm": (25.0, 40.0, ("sandstorm",)),
    "earthquake": (38.9, 35.2, ("earthquake",)),
    "quake": (38.9, 35.2, ("earthquake",)),
}


def _enrich_geo_entities(
    text: str,
) -> tuple[tuple[float, float] | None, tuple[str, ...]]:
    """Best-effort geo/entity extraction for live headlines."""
    low = text.lower()
    geo = None
    entities: list[str] = []
    for needle, (lat, lon, toks) in _GAZETTEER.items():
        if needle in low:
            geo = geo or (lat, lon)
            entities.extend(toks)
    return geo, tuple(dict.fromkeys(entities))


_HINT_KEYWORDS = [
    (r"hurricane|storm|typhoon|cyclone|tornado", "extreme_weather"),
    (r"flood|flooding", "flood"),
    (r"drought", "drought"),
    (r"earthquake|quake|seismic", "seismic"),
    (r"wildfire|fire", "fire"),
    (r"port|vessel|ship|rout|canal|suez|strait|container", "shipping_anomaly"),
    (r"explod|sabotage|pipeline|bridge|infrastructure", "infrastructure"),
    (r"strike|union|uaw|labor", "labor_disruption"),
    (r"covid|lockdown|pandemic", "pandemic"),
    (r"war|invad|missile|attack|conflict|sanction", "geopolitical"),
    (r"shortage|supply", "materials_shortage"),
    (r"price|rates|market|surge", "market"),
]


def _guess_hint(text: str) -> str:
    """Keyword-map a headline onto a severity kind hint."""
    import re as _re

    low = text.lower()
    for pat, hint in _HINT_KEYWORDS:
        if _re.search(pat, low):
            return hint
    return "advisory"


def gdelt_signals(
    query: str,
    start: str,
    end: str,
    *,
    max_records: int = 75,
    timeout: float = 30.0,
    default_geo: tuple[float, float] | None = None,
    default_entities: tuple[str, ...] = (),
) -> Iterator[Signal]:
    """Pull PIT-compliant news from GDELT DOC 2.1 within [start, end].

    `end` is the information cutoff. `default_geo`/`default_entities`
    anchor articles whose headlines carry no gazetteer match so the
    query topic still clusters geographically.
    """
    import subprocess
    import time

    import httpx

    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": max_records,
        "startdatetime": start,
        "enddatetime": end,
        "sort": "DateDesc",
    }
    articles: list[dict] = []

    for attempt in range(3):
        try:
            resp = httpx.get(
                GDELT_API,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; watchtower/1.0)"
                },
                params=params,
                timeout=timeout,
            )
        except httpx.HTTPError:
            resp = None
        else:
            if resp.status_code == 200:
                try:
                    articles = resp.json().get("articles", [])
                    if articles:
                        break
                except (json.JSONDecodeError, ValueError):
                    articles = []
        # GDELT asks for one request per 5s; honor it on every retry.
        time.sleep(6)

    if not articles:
        # GDELT occasionally serves empty/HTML bodies to httpx; curl
        # with --data-urlencode is the reliable fallback.
        out = subprocess.run(
            [
                "curl", "-s", "-m", "30", "-G", GDELT_API,
                "--data-urlencode", f"query={query}",
                "--data-urlencode", "mode=ArtList",
                "--data-urlencode", "format=json",
                "--data-urlencode", f"maxrecords={max_records}",
                "--data-urlencode", f"startdatetime={start}",
                "--data-urlencode", f"enddatetime={end}",
                "--data-urlencode", "sort=DateDesc",
            ],
            capture_output=True,
            check=False,
        ).stdout
        try:
            articles = json.loads(out).get("articles", [])
        except (json.JSONDecodeError, ValueError):
            articles = []

    signals = []
    for art in articles:
        ts_raw = art.get("seendate", "")  # e.g. "20220224T073000Z"
        try:
            dt = datetime.strptime(ts_raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        geo_e, ents = _enrich_geo_entities(art.get("title", ""))
        signals.append(
            Signal(
                ts=dt.timestamp(),
                source_type="wire",
                source_name=art.get("domain", "unknown"),
                url=art.get("url", ""),
                text=art.get("title", ""),
                geo=geo_e or default_geo,
                entities=ents or default_entities,
                lang=art.get("language", "en"),
                kind_hint=_guess_hint(art.get("title", "")),
                credibility=0.6,
                signal_id=_signal_id(
                    dt.timestamp(), art.get("domain", ""), art.get("title", "")
                ),
            )
        )
    yield from sorted(signals, key=lambda s: s.ts)


EONET_API = "https://eonet.gsfc.nasa.gov/api/v3/events"

# EONET category -> kind_hint (drives severity + mechanism tags)
_EONET_HINT = {
    "severeStorms": "extreme_weather",
    "wildfires": "fire",
    "volcanoes": "infrastructure",
    "earthquakes": "seismic",
    "floods": "flood",
    "landslides": "infrastructure",
    "drought": "drought",
    "dustHaze": "extreme_weather",
    "snow": "extreme_weather",
    "tempExtremes": "extreme_weather",
    "seaLakeIce": "extreme_weather",
    "waterColor": "flood",
    "manmade": "infrastructure",
}

_EONET_CRED = {"Green": 0.5, "Orange": 0.7, "Red": 0.85}


def eonet_signals(
    start: str,
    end: str,
    *,
    status: str = "all",
    limit: int = 100,
    timeout: float = 30.0,
) -> Iterator[Signal]:
    """Pull PIT-compliant natural events from NASA EONET.

    `start`/`end` are ISO dates (YYYY-MM-DD); `end` is the information
    cutoff. Each event's most recent geometry point provides geo; the
    EONET category maps onto our severity kind vocabulary so live
    hazards score correctly out of the box.
    """
    import httpx

    params = {
        "status": status,
        "limit": limit,
        "start": start,
        "end": end,
    }
    resp = httpx.get(EONET_API, params=params, timeout=timeout)
    resp.raise_for_status()
    events = resp.json().get("events", [])

    signals = []
    for ev in events:
        geom = ev.get("geometry") or []
        if not geom:
            continue
        last = geom[-1]
        coords = last.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        try:
            dt = datetime.fromisoformat(
                last["date"].replace("Z", "+00:00")
            )
        except (KeyError, ValueError):
            continue
        cat = (ev.get("categories") or [{}])[0].get("id", "manmade")
        title = ev.get("title", "")
        geo_e, ents = _enrich_geo_entities(title)
        hint = _EONET_HINT.get(cat, "infrastructure")
        mag = ev.get("magnitudeValue")
        cred = 0.85  # satellite/official source
        signals.append(
            Signal(
                ts=dt.timestamp(),
                source_type="govt_advisory",
                source_name="NASA EONET",
                url=ev.get("link", ""),
                text=f"[{cat}] {title}" + (f" ({mag})" if mag else ""),
                geo=(lat, lon),
                entities=ents,
                lang="en",
                kind_hint=hint,
                credibility=cred,
                signal_id=_signal_id(dt.timestamp(), "eonet", title),
            )
        )
    yield from sorted(signals, key=lambda s: s.ts)
