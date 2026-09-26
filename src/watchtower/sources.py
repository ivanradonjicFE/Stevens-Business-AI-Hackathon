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
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from watchtower.config import (
    FeedSet,
    load_chokepoints,
    load_feeds,
    load_markets,
    load_weather,
)
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
                headers={"User-Agent": "Mozilla/5.0 (compatible; watchtower/1.0)"},
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
                "curl",
                "-s",
                "-m",
                "30",
                "-G",
                GDELT_API,
                "--data-urlencode",
                f"query={query}",
                "--data-urlencode",
                "mode=ArtList",
                "--data-urlencode",
                "format=json",
                "--data-urlencode",
                f"maxrecords={max_records}",
                "--data-urlencode",
                f"startdatetime={start}",
                "--data-urlencode",
                f"enddatetime={end}",
                "--data-urlencode",
                "sort=DateDesc",
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
    # EONET is unthrottled-free but slow at high limits; retry on
    # transient read timeouts so a single hiccup doesn't sink a demo.
    events = []
    for attempt in range(3):
        try:
            resp = httpx.get(EONET_API, params=params, timeout=timeout)
            resp.raise_for_status()
            events = resp.json().get("events", [])
            break
        except httpx.ReadTimeout:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))

    signals = []
    keep = {
        "severeStorms",
        "wildfires",
        "volcanoes",
        "earthquakes",
        "floods",
        "drought",
        "dustHaze",
        "snow",
        "tempExtremes",
    }
    for ev in events:
        cat0 = (ev.get("categories") or [{}])[0].get("id", "")
        if cat0 not in keep:
            continue
        geom = ev.get("geometry") or []
        if not geom:
            continue
        cat = (ev.get("categories") or [{}])[0].get("id", "manmade")
        title = ev.get("title", "")
        hint = _EONET_HINT.get(cat, "infrastructure")
        mag = ev.get("magnitudeValue")
        text = f"[{cat}] {title}" + (f" ({mag})" if mag else "")
        url = ev.get("link", "")
        cred = 0.85  # satellite/official source

        # entities: gazetteer match, else derive from the event title
        # itself (e.g. "Hurricane Ida" -> ("hurricane", "hurricane ida",
        # "ida")) so geographically-coherent events still cluster.
        geo_e, ents = _enrich_geo_entities(title)
        if not ents:
            toks = [w for w in title.lower().split() if len(w) > 3]
            ents = (tuple(toks[:2]) + (title.lower(),)) if toks else ()

        # Emit ONE signal per event at the track/footprint midpoint —
        # storm tracks emit geometry-per-timestamp; per-point signals
        # flood the correlator with fragments of the same event.
        pts = []
        for g in geom:
            coords = g.get("coordinates") or []
            if len(coords) < 2:
                continue
            if g.get("type") == "Polygon":
                ring = (
                    coords[0]
                    if coords and isinstance(coords[0][0], (list, tuple))
                    else coords
                )
                xs = [c[0] for c in ring]
                ys = [c[1] for c in ring]
                lon, lat = sum(xs) / len(xs), sum(ys) / len(ys)
            else:
                lon, lat = float(coords[0]), float(coords[1])
            pts.append((lon, lat, g.get("date", "")))
        if not pts:
            continue
        lon = sum(p[0] for p in pts) / len(pts)
        lat = sum(p[1] for p in pts) / len(pts)
        # Anchor to the nearest supply-chain node: adds the node name
        # to entities (for clustering when the title carries no gazetteer
        # hit) and to text (the relevance cosine scores signal.text, so
        # geo terms give it real supply-chain vocabulary to match on).
        text, ents = anchor_to_supply_node(lat, lon, text, ents)
        mid = pts[len(pts) // 2][2] or pts[-1][2]
        try:
            dt = datetime.fromisoformat(mid.replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        signals.append(
            Signal(
                ts=dt.timestamp(),
                source_type="govt_advisory",
                source_name="NASA EONET",
                url=url,
                text=text,
                geo=(lat, lon),
                entities=ents,
                lang="en",
                kind_hint=hint,
                credibility=cred,
                signal_id=_signal_id(dt.timestamp(), "eonet", title),
            )
        )
    yield from sorted(signals, key=lambda s: s.ts)


# --- hazard scouts ----------------------------------------------------------
#
# The hazard half of the scouting layer: authoritative disaster feeds for
# the natural perils most likely to idle a fab, an OSAT or a port. Tropical
# cyclones (GDACS, NASA EONET), tsunamis (NOAA warning centres, and the
# tsunami-flagged USGS quakes behind them) and the other GDACS perils
# (flood, wildfire, volcano, drought). Breaking news stays GDELT's job.
#
# Every collector takes an information cutoff (PIT) and drops anything
# newer, so a situation report can be regenerated as of a past instant.

GDACS_API = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
USGS_QUAKES = (
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
)
NOAA_TSUNAMI_FEEDS = {
    "NTWC (Alaska)": "https://www.tsunami.gov/events/xml/PAAQAtom.xml",
    "PTWC (Hawaii)": "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
}

# GDACS event type -> kind_hint (drives severity + analog mechanisms).
_GDACS_HINT = {
    "TC": "extreme_weather",
    "TS": "tsunami",
    "FL": "flood",
    "WF": "fire",
    "VO": "infrastructure",
    "DR": "drought",
    "EQ": "seismic",
}
# GDACS alert level -> credibility prior (the authority's own call).
_GDACS_CREDIBILITY = {"Red": 0.95, "Orange": 0.9, "Green": 0.8}
# NOAA bulletin category -> (kind_hint, credibility).
_NOAA_ALERT = {
    "warning": ("tsunami", 0.95),
    "threat": ("tsunami", 0.95),
    "advisory": ("tsunami", 0.9),
    "watch": ("tsunami", 0.9),
}

_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
}
_STORM_NAME = re.compile(
    r"(?:cyclone|typhoon|hurricane|tropical storm|storm)\s+([A-Za-z][A-Za-z-]*)",
    re.IGNORECASE,
)
_ANCHOR_RADIUS_KM = 1500.0
_QUAKE_MATCH_KM = 300.0  # same quake reported by NOAA and USGS


def anchor_to_supply_node(
    lat: float, lon: float, text: str, entities: tuple[str, ...]
) -> tuple[str, tuple[str, ...]]:
    """Tie a hazard to the nearest monitored supply-chain node.

    Adds the node name to ``entities`` (so a hazard clusters with the
    node even when its headline carries no gazetteer hit) and to
    ``text`` (the relevance cosine scores signal text, so geographic
    vocabulary gives it something real to match on). Returns the input
    unchanged when the nearest node is out of range.
    """
    try:
        from watchtower.config import load_chokepoints
        from watchtower.geo import haversine_km

        nearest = min(
            load_chokepoints(), key=lambda n: haversine_km((lat, lon), n.geo)
        )
        dist_km = haversine_km((lat, lon), nearest.geo)
    except (ImportError, FileNotFoundError, KeyError, ValueError):
        # chokepoint data unavailable: degrade, don't hide the hazard
        return text, entities or ("natural_hazard",)
    if dist_km > _ANCHOR_RADIUS_KM:
        return text, entities
    if not entities:
        entities = tuple(
            word.lower()
            for word in nearest.name.replace("/", " ").split()
            if len(word) > 3
        ) or ("natural_hazard",)
    return f"{text} (nearest supply node: {nearest.name}, {dist_km:.0f}km)", entities


_COMPACT_TS = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$")


def _day_end_ts(day: str) -> float:
    """Cutoff instant for a date or timestamp.

    Accepts ISO (``2026-09-25``, ``2026-09-25T08:00:00Z``) and the compact
    ``YYYYMMDDHHMMSS`` form GDELT uses, so one cutoff can be threaded through
    every source without each caller reformatting it. A bare date means the
    end of that UTC day.
    """
    raw = day.strip()
    compact = _COMPACT_TS.match(raw)
    if compact:
        raw = "{}-{}-{}T{}:{}:{}Z".format(*compact.groups())
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if len(raw) <= 10:  # a bare date means the whole day
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.timestamp()


def _fetch(
    url: str,
    params: dict | None = None,
    *,
    timeout: float,
    retries: int = 2,
):
    """GET with a couple of retries so one hiccup doesn't sink a run."""
    import httpx

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError(f"no response from {url}")


def _storm_name(text: str) -> str:
    """Storm name from a headline: "Tropical Cyclone BAVI-26" -> "bavi"."""
    match = _STORM_NAME.search(text)
    return match.group(1).split("-")[0].lower() if match else ""


def _parse_gdacs(payload: dict, end_ts: float) -> list[Signal]:
    """GDACS GeoJSON -> signals, one per hazard, cut off at ``end_ts``."""
    signals: list[Signal] = []
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        event_type = str(props.get("eventtype", ""))
        hint = _GDACS_HINT.get(event_type)
        if hint is None:
            continue
        started = _parse_ts(str(props.get("fromdate", "")))
        if started > end_ts:
            continue
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        alert = str(props.get("alertlevel", "Green"))
        name = str(props.get("name", "")).strip() or f"GDACS {event_type}"
        countries = [
            str(c.get("countryname", ""))
            for c in props.get("affectedcountries") or []
        ]
        places = [c for c in countries if c]
        text = f"{name} ({', '.join(places)})" if places else name
        try:
            severity = float((props.get("severitydata") or {})["severity"])
        except (KeyError, TypeError, ValueError):
            severity = None
        if severity is not None:
            unit = "km/h winds" if event_type == "TC" else "M"
            text += f" - {severity:g} {unit}"
        text += f" [GDACS {alert} alert]"
        text, entities = anchor_to_supply_node(lat, lon, text, ())
        signals.append(
            Signal(
                ts=started,
                source_type="govt_advisory",
                source_name=f"GDACS ({event_type})",
                url=str((props.get("url") or {}).get("report", "")),
                text=text,
                geo=(lat, lon),
                entities=entities,
                lang="en",
                kind_hint=hint,
                credibility=_GDACS_CREDIBILITY.get(alert, 0.8),
                signal_id=_signal_id(started, f"gdacs-{event_type}", name),
            )
        )
    return signals


def _parse_usgs(payload: dict, end_ts: float) -> list[Signal]:
    """USGS GeoJSON -> signals for tsunami-flagged quakes only.

    The flag is USGS's own tsunami-potential assessment, so these are the
    quakes that can actually generate the ocean wave NOAA then bulletins.
    """
    signals: list[Signal] = []
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        if not props.get("tsunami"):
            continue
        ts = float(props.get("time") or 0.0) / 1000.0
        if not ts or ts > end_ts:
            continue
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        try:
            magnitude = float(props["mag"])
        except (KeyError, TypeError, ValueError):
            magnitude = None
        # PAGER alert when USGS has published one, else magnitude rules
        alerts = {"red": "Red", "orange": "Orange"}
        alert = alerts.get(str(props.get("alert") or "").lower())
        if alert is None:
            alert = "Orange" if (magnitude or 0.0) >= 6.5 else "Green"
        text = f"M{magnitude:.1f} earthquake, tsunami potential flagged" if magnitude else "Earthquake, tsunami potential flagged"
        place = str(props.get("place", "")).strip()
        if place:
            text += f" - {place}"
        text += f" [USGS {alert} alert]"
        text, entities = anchor_to_supply_node(
            lat, lon, text, ("tsunami", "earthquake")
        )
        signals.append(
            Signal(
                ts=ts,
                source_type="govt_advisory",
                source_name="USGS Earthquake Hazards Program",
                url=str(props.get("url", "")),
                text=text,
                geo=(lat, lon),
                entities=entities,
                lang="en",
                kind_hint="tsunami",
                credibility=0.95,
                signal_id=_signal_id(
                    ts, "usgs", str(props.get("ids") or props.get("id") or place)
                ),
            )
        )
    return signals


def _parse_noaa_tsunami(
    xml_text: str, end_ts: float, center: str
) -> list[Signal]:
    """NOAA tsunami warning-centre Atom feed -> signals.

    Structure comes from the XML (entry, updated, link, geo point); the
    category and magnitude live in an HTML blob inside the entry, so
    those two are read with a regex.
    """
    signals: list[Signal] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return signals
    for entry in root.findall("atom:entry", _ATOM_NS):
        updated = entry.findtext("atom:updated", default="", namespaces=_ATOM_NS)
        title = (entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or "").strip()
        if not updated or not title:
            continue
        ts = _parse_ts(updated)
        if ts > end_ts:
            continue
        blob = entry.findtext("atom:content", default="", namespaces=_ATOM_NS) or ""
        blob += entry.findtext("atom:summary", default="", namespaces=_ATOM_NS) or ""
        found = re.search(r"Category:\s*</strong>\s*([A-Za-z ]+)", blob)
        category = " ".join(found.group(1).split()) if found else "Information"
        magnitude = re.search(r"Magnitude:\s*</strong>\s*([\d.]+)", blob)
        link = ""
        for node in entry.findall("atom:link", _ATOM_NS):
            if node.get("rel") == "alternate" and node.get("href"):
                link = str(node.get("href"))
                break
        geo = None
        lat = entry.findtext("geo:lat", default="", namespaces=_ATOM_NS)
        lon = entry.findtext("geo:long", default="", namespaces=_ATOM_NS)
        try:
            geo = (float(lat), float(lon))
        except (TypeError, ValueError):
            geo = None
        hint, credibility = _NOAA_ALERT.get(category.lower(), ("tsunami", 0.8))
        text = f"Tsunami {category.lower()} - {title}"
        if magnitude:
            text += f" (M{magnitude.group(1)})"
        text += f" [NOAA {center}]"
        entities: tuple[str, ...] = ("tsunami",)
        if geo:
            text, entities = anchor_to_supply_node(geo[0], geo[1], text, entities)
        signals.append(
            Signal(
                ts=ts,
                source_type="govt_advisory",
                source_name=f"NOAA {center}",
                url=link or "https://www.tsunami.gov/",
                text=text,
                geo=geo,
                entities=entities,
                lang="en",
                kind_hint=hint,
                credibility=credibility,
                signal_id=_signal_id(ts, f"noaa-{center}", title),
            )
        )
    return signals


def gdacs_signals(
    start: str,
    end: str,
    *,
    event_types: tuple[str, ...] = ("TC",),
    timeout: float = 30.0,
) -> Iterator[Signal]:
    """Pull PIT-compliant hazards from GDACS within [start, end].

    ``start``/``end`` are ISO dates; ``end`` is the information cutoff.
    Tropical cyclones are the default because those are what threaten
    fabs and ports; pass other GDACS event types to widen the net.
    """
    end_ts = _day_end_ts(end)
    collected: list[Signal] = []
    for event_type in event_types:
        payload = _fetch(
            GDACS_API,
            params={
                "eventlist": event_type,
                "fromDate": start.strip()[:10],
                "toDate": end.strip()[:10],
            },
            timeout=timeout,
        ).json()
        collected.extend(_parse_gdacs(payload, end_ts))
    yield from sorted(collected, key=lambda s: s.ts)


def usgs_tsunami_signals(
    end: str, *, timeout: float = 20.0
) -> Iterator[Signal]:
    """Tsunami-flagged USGS quakes from the past week, cut off at ``end``.

    USGS publishes rolling windows rather than arbitrary ranges, so the
    cutoff is applied to the feed's own timestamps.
    """
    payload = _fetch(USGS_QUAKES, timeout=timeout).json()
    yield from sorted(_parse_usgs(payload, _day_end_ts(end)), key=lambda s: s.ts)


def noaa_tsunami_signals(
    end: str, *, lookback_days: int = 10, timeout: float = 20.0
) -> Iterator[Signal]:
    """NOAA tsunami bulletins (NTWC + PTWC) cut off at ``end``."""
    end_ts = _day_end_ts(end)
    oldest = end_ts - max(1, lookback_days) * 86400
    collected: list[Signal] = []
    for center, url in NOAA_TSUNAMI_FEEDS.items():
        text = _fetch(url, timeout=timeout).text
        collected.extend(
            s for s in _parse_noaa_tsunami(text, end_ts, center) if s.ts >= oldest
        )
    yield from sorted(collected, key=lambda s: s.ts)


def _dedupe_hazards(signals: list[Signal]) -> list[Signal]:
    """Drop hazards a second authority has already reported.

    The grounds for each drop are physical: the same quake (USGS and NOAA
    within 300 km and a day) or the same named storm (GDACS numbers its
    cyclones, EONET doesn't). Keeping both would double-count
    corroboration in the correlator and inflate severity.
    """
    from watchtower.geo import haversine_km

    bulletined = [
        (s.ts, s.geo) for s in signals if s.source_name.startswith("NOAA")
    ]
    gdacs_storms = {
        name
        for s in signals
        if s.source_name.startswith("GDACS") and (name := _storm_name(s.text))
    }
    out: list[Signal] = []
    for signal in signals:
        if signal.source_name.startswith("USGS") and signal.geo:
            duplicate = any(
                geo is not None
                and haversine_km(signal.geo, geo) < _QUAKE_MATCH_KM
                and abs(signal.ts - ts) < 86400
                for ts, geo in bulletined
            )
            if duplicate:
                continue
        elif signal.source_name.startswith(("NASA EONET", "NHC")):
            # EONET and the NHC name storms the way GDACS does, so the same
            # rule folds them together instead of double-counting one cyclone
            # as three independent corroborations.
            name = _storm_name(signal.text)
            if name and name in gdacs_storms:
                continue
        out.append(signal)
    return out


# --- news: keyless RSS/Atom, plus GDELT for query-parametric search ---------

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

#: Google News answers a search with ~100 items; keep the newest slice so one
#: query cannot dominate a report's signal set.
NEWS_QUERY_LIMIT = 40

_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_FEED_FIELDS = (
    "title",
    "link",
    "description",
    "summary",
    "pubdate",
    "published",
    "updated",
    "source",
)


def search_feed_url(
    query: str, *, hl: str = "en-US", gl: str = "US", ceid: str = "US:en"
) -> str:
    """Keyless Google News RSS search URL for ``query``.

    Unofficial and undocumented: it rate-limits, its links are redirects
    rather than canonical publisher URLs, and its shape can change without
    notice. That is why it is used alongside GDELT and the publisher feeds
    and never as the only news source.
    """
    from urllib.parse import urlencode

    return f"{GOOGLE_NEWS_RSS}?{urlencode({'q': query, 'hl': hl, 'gl': gl, 'ceid': ceid})}"


def _plain(text: str) -> str:
    """Strip markup and collapse whitespace from a feed field."""
    return _WHITESPACE.sub(" ", _HTML_TAG.sub(" ", text or "")).strip()


def _localname(tag: str) -> str:
    """An element tag without its XML namespace (``{ns}title`` -> ``title``)."""
    return tag.rsplit("}", 1)[-1].lower()


def _feed_items(text: str) -> tuple[str, list[dict[str, str]]]:
    """Pull the channel title and item fields out of an RSS 2.0/Atom document.

    Both formats are handled from the same walk because publishers are
    inconsistent about which they serve, and Google News RSS is the only
    one we can rely on being RSS 2.0. The channel title is returned because a
    feed knows its own name, which beats guessing it from the URL host.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        log.warning("rss: unparseable feed: %s", exc)
        return "", []
    feed_title = ""
    for node in root.iter():
        if _localname(node.tag) == "title":
            feed_title = _plain(node.text or "")
            break
    items: list[dict[str, str]] = []
    for node in root.iter():
        if _localname(node.tag) not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        for child in node:
            name = _localname(child.tag)
            if name not in _FEED_FIELDS:
                continue
            value = (child.text or "").strip()
            # Atom puts the URL in the attribute, RSS in the element text
            if name == "link" and not value:
                value = child.get("href", "")
            fields.setdefault(name, value)
        items.append(fields)
    return feed_title, items


def _feed_ts(fields: dict[str, str]) -> float | None:
    """Publication instant from an RSS ``pubDate`` or an Atom timestamp."""
    from email.utils import parsedate_to_datetime

    raw = (
        fields.get("pubdate")
        or fields.get("published")
        or fields.get("updated")
        or ""
    ).strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return _parse_ts(raw)
    except ValueError:
        return None


def rss_signals(
    url: str,
    end: str,
    *,
    source_name: str | None = None,
    source_type: str = "trade_press",
    credibility: float = 0.55,
    limit: int | None = None,
    timeout: float = 20.0,
) -> Iterator[Signal]:
    """Pull PIT-bounded headlines from one keyless RSS 2.0 or Atom feed.

    ``end`` is the information cutoff (a bare date means the end of that
    UTC day); items published after it are dropped, which is what keeps a
    live feed replayable as of a past instant. ``source_name`` defaults to
    the item's own publisher when the feed names one - Google News does.
    ``limit`` keeps the newest N items, which matters for search feeds that
    return a hundred results per query.
    """
    response = _fetch(url, timeout=timeout)
    cutoff = _day_end_ts(end)
    feed_title, items = _feed_items(response.text)
    signals: list[Signal] = []
    for fields in items:
        ts = _feed_ts(fields)
        if ts is None or ts > cutoff:
            continue
        title = _plain(fields.get("title", ""))
        publisher = _plain(fields.get("source", ""))
        if publisher and title.endswith(f" - {publisher}"):
            # Google News suffixes the publisher onto the headline
            title = title[: -(len(publisher) + 3)].strip()
        body = _plain(fields.get("description") or fields.get("summary") or "")
        text = _WHITESPACE.sub(" ", f"{title}. {body}").strip(". ").strip()
        if not title:
            continue
        name = source_name or publisher or feed_title or url.split("/")[2]
        geo, entities = _enrich_geo_entities(text)
        signals.append(
            Signal(
                ts=ts,
                source_type=source_type,
                source_name=name,
                url=fields.get("link") or url,
                text=text[:600] or title,
                geo=geo,
                entities=entities,
                lang="en",
                kind_hint=_guess_hint(text),
                credibility=credibility,
                signal_id=_signal_id(ts, name, text),
            )
        )
    signals.sort(key=lambda s: s.ts, reverse=True)
    if limit is not None:
        signals = signals[:limit]
    # ascending, like every other collector, so a replay consumes in order
    yield from sorted(signals, key=lambda s: s.ts)


def _title_key(text: str) -> str:
    """Editorial identity of a story: its punctuation-free leading words."""
    low = re.sub(r"[^a-z0-9 ]+", " ", _plain(text).lower())
    return _WHITESPACE.sub(" ", low).strip()[:120]


def _dedupe_news(signals: list[Signal]) -> list[Signal]:
    """Drop the same story arriving twice.

    Unlike the hazard de-dupe (which matches physical identity), news needs
    *editorial* identity: the same link, or the same headline syndicated
    across publishers - which is exactly what happens when GDELT, a
    publisher feed and Google News all carry one wire story.
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[Signal] = []
    for signal in signals:
        url_key = signal.url.split("?")[0].rstrip("/").lower()
        title_key = _title_key(signal.text)
        if url_key and url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue
        if url_key:
            seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        out.append(signal)
    return out


def news_signals(
    start: str,
    end: str,
    *,
    market_key: str = "semicon",
    gdelt: bool = False,
    timeout: float = 30.0,
    feeds: FeedSet | None = None,
) -> Iterator[Signal]:
    """Run every free news source for a market and de-duplicate the result.

    One dead source is logged and skipped, exactly like the hazard scouts, so
    a report still goes out with whatever answered.

    GDELT is **off by default**: it is rate-limited to one request per 5s,
    bans shared IPs, and its retry path costs over a minute before giving up
    - measured at 63s for a single query that then returned nothing. The RSS
    sources answer in about a second and between them yield far more
    headlines, so GDELT is the opt-in extra (``--live QUERY``) rather than
    the thing the run waits on.
    """
    registry = feeds if feeds is not None else load_feeds()
    collected: list[Signal] = []

    for query in registry.queries_for(market_key):
        if gdelt:
            try:
                collected.extend(gdelt_signals(query, start, end, timeout=timeout))
            except Exception as exc:  # noqa: BLE001 - one dead source must not stop the run
                log.warning("news source gdelt(%s) failed: %s", query[:40], exc)
        try:
            # No source_name: Google News names the publisher per item, and that
            # is better provenance than the aggregator. Credibility stays low
            # because the aggregation itself is unverified.
            collected.extend(
                rss_signals(
                    search_feed_url(query),
                    end,
                    credibility=0.5,
                    limit=NEWS_QUERY_LIMIT,
                    timeout=timeout,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("news source google-news(%s) failed: %s", query[:40], exc)

    for spec in registry.feeds:
        try:
            collected.extend(
                rss_signals(
                    spec.url,
                    end,
                    source_name=spec.name,
                    source_type=spec.source_type,
                    credibility=spec.credibility,
                    timeout=timeout,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("news source %s failed: %s", spec.name, exc)

    yield from _dedupe_news(sorted(collected, key=lambda s: s.ts))


def hazard_signals(
    start: str, end: str, *, timeout: float = 30.0
) -> Iterator[Signal]:
    """Run every hazard scout for a window and de-duplicate the result.

    A single dead feed is logged and skipped: a report run should still
    go out with the authorities that answered.
    """
    end_ts = _day_end_ts(end)
    lookback = max(1, int((end_ts - _parse_ts(start)) / 86400) + 1)
    scouts = (
        ("gdacs", lambda: list(gdacs_signals(start, end, timeout=timeout))),
        ("usgs", lambda: list(usgs_tsunami_signals(end, timeout=timeout))),
        (
            "noaa_tsunami",
            lambda: list(
                noaa_tsunami_signals(end, lookback_days=lookback, timeout=timeout)
            ),
        ),
    )
    collected: list[Signal] = []
    for name, scout in scouts:
        try:
            collected.extend(scout())
        except Exception as exc:  # noqa: BLE001 - one dead source must not stop the run
            log.warning("hazard scout %s failed: %s", name, exc)
    yield from sorted(_dedupe_hazards(collected), key=lambda s: s.ts)


# --- weather: keyless forecasts (Open-Meteo, GloFAS, NHC, NWS) --------------
#
# A weather signal is the earliest thing in the pipeline: it says what is
# coming rather than what happened. That also makes it the easiest thing to
# fake, so every collector here states its basis (model, day, lead time) in
# the signal text and carries a lower credibility prior than an authority
# alert. Threshold gates in ``data/weather.yaml`` keep it from drowning the
# correlator in weather.

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
OPEN_METEO_FLOOD = "https://flood-api.open-meteo.com/v1/flood"
OPEN_METEO_PREVIOUS = "https://previous-runs-api.open-meteo.com/v1/forecast"
NHC_STORMS = "https://www.nhc.noaa.gov/CurrentStorms.json"
NWS_ALERTS = "https://api.weather.gov/alerts/active"

_FORECAST_DAILY = (
    "wind_speed_10m_max,wind_gusts_10m_max,precipitation_sum,"
    "temperature_2m_max,temperature_2m_min"
)

#: (metric, label, kind_hint, direction). Direction ``le`` is the cold tail.
_HAZARD_RULES = (
    ("wind_speed_10m_max", "sustained winds", "extreme_weather", "ge"),
    ("wind_gusts_10m_max", "wind gusts", "extreme_weather", "ge"),
    ("precipitation_sum", "24h rainfall", "flood", "ge"),
    ("temperature_2m_max", "extreme heat", "extreme_weather", "ge"),
    ("temperature_2m_min", "extreme cold", "extreme_weather", "le"),
)

#: Previous-run backtesting: (hourly variable, how to fold a day's hours,
#: label, metric key, kind_hint, direction). The daily maxima and the daily
#: precipitation total are rebuilt from the hourly series because the
#: previous-runs API only exposes ``*_previous_dayN`` on hourly variables.
_PREVIOUS_RULES = (
    ("wind_speed_10m", "max", "sustained winds", "wind_speed_10m_max", "extreme_weather", "ge"),
    ("wind_gusts_10m", "max", "wind gusts", "wind_gusts_10m_max", "extreme_weather", "ge"),
    ("precipitation", "sum", "24h rainfall", "precipitation_sum", "flood", "ge"),
    ("temperature_2m", "max", "extreme heat", "temperature_2m_max", "extreme_weather", "ge"),
    ("temperature_2m", "min", "extreme cold", "temperature_2m_min", "extreme_weather", "le"),
)

_UNITS = {
    "wind_speed_10m_max": "km/h",
    "wind_gusts_10m_max": "km/h",
    "precipitation_sum": "mm",
    "temperature_2m_max": "C",
    "temperature_2m_min": "C",
}

#: A live feed describes the present, so it may only answer a cutoff that is
#: effectively now. An older cutoff is answered from the archived-forecast
#: endpoint, or not at all - anything else would let a report dated in the past
#: read today's forecast, which is exactly the lookahead PIT forbids.
FORECAST_LIVE_WINDOW_S = 2 * 86400

#: Forecasts are projections, not observations: below a government advisory
#: (0.9+) but above unattributed social chatter.
FORECAST_CREDIBILITY = 0.55

#: Node kinds where river level actually decides throughput.
RIVER_NODE_KINDS = frozenset({"canal", "port", "material"})

#: The floor below which a discharge series is too short to call a percentile.
_MIN_BASELINE_DAYS = 20

#: A live dashboard polls these repeatedly and the free tier allows ~10k
#: calls/day, so a response is cached rather than re-asked on every tick.
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL_S = 3600.0


def clear_cache() -> None:
    """Drop cached weather responses (tests, and long-lived processes)."""
    _CACHE.clear()


def _cached_json(url: str, params: dict, *, timeout: float) -> dict:
    """GET and cache one JSON document. Still goes through ``_fetch``."""
    key = (url, tuple(sorted(params.items())))
    now = time.time()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    payload = _fetch(url, params, timeout=timeout).json()
    _CACHE[key] = (now, payload)
    return payload


def _live_only(end_ts: float, name: str) -> bool:
    """May a live-only feed answer this cutoff? (Else it would leak.)"""
    if end_ts < time.time() - FORECAST_LIVE_WINDOW_S:
        log.info(
            "%s is a live feed and cannot answer a past cutoff; skipping", name
        )
        return False
    return True


def _thresholds(spec) -> dict[str, float]:
    """Metric name -> the level it must cross to become a signal."""
    return {
        "wind_speed_10m_max": spec.thresholds.wind_kmh,
        "wind_gusts_10m_max": spec.thresholds.gust_kmh,
        "precipitation_sum": spec.thresholds.rain_mm_24h,
        "temperature_2m_max": spec.thresholds.heat_c,
        "temperature_2m_min": spec.thresholds.cold_c,
    }


def _extreme(series: list, direction: str) -> tuple[int, float] | None:
    """Index and value of the worst day in a daily series."""
    pairs = [
        (index, float(value))
        for index, value in enumerate(series)
        if isinstance(value, (int, float))
    ]
    if not pairs:
        return None
    pick = max if direction == "ge" else min
    return pick(pairs, key=lambda pair: pair[1])


def _percentile(values: list[float], percent: float) -> float:
    """Value at ``percent`` of ``values`` (nearest-rank, no interpolation)."""
    ordered = sorted(values)
    index = int(round(percent / 100 * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, index))]


def _day_of(day: str, fallback: date) -> date | None:
    """Parse an Open-Meteo day string, or ``None`` when it is malformed."""
    try:
        return date.fromisoformat((day or "")[:10])
    except ValueError:
        return fallback


def _forecast_signal(
    node,
    *,
    day: str,
    lead: int,
    label: str,
    value: float,
    unit: str,
    kind: str,
    direction: str,
    ts: float,
    basis: str = "Open-Meteo multi-model blend",
) -> Signal:
    """One threshold-crossing forecast, stated with its basis and horizon."""
    phrase = "up to" if direction == "ge" else "down to"
    text = (
        f"forecast: {label} {phrase} {value:.0f} {unit} at {node.name} on "
        f"{day} (+{lead}d), {basis}"
    )
    anchored, entities = anchor_to_supply_node(
        node.geo[0], node.geo[1], text, (node.name.lower(),)
    )
    return Signal(
        ts=ts,
        source_type="analyst",
        source_name="Open-Meteo",
        url="https://open-meteo.com/",
        text=anchored,
        geo=node.geo,
        entities=entities,
        kind_hint=kind,
        credibility=FORECAST_CREDIBILITY,
        signal_id=_signal_id(ts, f"openmeteo-{label}", anchored),
    )


def openmeteo_forecast_signals(
    nodes, end: str, *, spec=None, timeout: float = 30.0
) -> Iterator[Signal]:
    """Forecast signals: what the models say is coming at each node.

    Only threshold crossings become signals, and each carries the day it
    happens and its lead time - "90 km/h winds in 3 days" is actionable in a
    way that a current observation is not. A cutoff older than the live window
    is answered from the archived forecasts, so a past report stays honest.

    ``data/weather.yaml``: this is one request per node, so ~26 nodes per run.
    """
    weather = spec if spec is not None else load_weather()
    end_ts = _day_end_ts(end)
    stamp = min(end_ts, time.time())
    cutoff = datetime.fromtimestamp(end_ts, UTC).date()
    if end_ts < time.time() - FORECAST_LIVE_WINDOW_S:
        finish = (cutoff + timedelta(days=weather.horizon_days)).isoformat()
        url = OPEN_METEO_ARCHIVE
        window = {"start_date": cutoff.isoformat(), "end_date": finish}
    else:
        url = OPEN_METEO_FORECAST
        window = {"forecast_days": weather.horizon_days}
    limits = _thresholds(weather)
    out: list[Signal] = []
    for node in nodes:
        params = {
            "latitude": node.geo[0],
            "longitude": node.geo[1],
            "timezone": "UTC",
            "daily": _FORECAST_DAILY,
            **window,
        }
        try:
            payload = _cached_json(url, params, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - one dead node must not stop the run
            log.warning("weather forecast failed for %s: %s", node.name, exc)
            continue
        daily = payload.get("daily") or {}
        days = daily.get("time") or []
        for metric, label, kind, direction in _HAZARD_RULES:
            worst = _extreme(daily.get(metric) or [], direction)
            if worst is None:
                continue
            index, value = worst
            level = limits[metric]
            crossed = value >= level if direction == "ge" else value <= level
            if not crossed:
                continue
            day = days[index] if index < len(days) else ""
            when = _day_of(day, cutoff)
            lead = max(0, (when - cutoff).days) if when else 0
            out.append(
                _forecast_signal(
                    node,
                    day=day,
                    lead=lead,
                    label=label,
                    value=value,
                    unit=_UNITS[metric],
                    kind=kind,
                    direction=direction,
                    ts=stamp,
                )
            )
    yield from sorted(out, key=lambda s: s.ts)


def _fold(values: list, how: str) -> float | None:
    """Fold a day's hourly values into the daily metric (max/min/sum)."""
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    if not numbers:
        return None
    if how == "max":
        return max(numbers)
    if how == "min":
        return min(numbers)
    return sum(numbers)


def openmeteo_previous_run_signals(
    nodes,
    end: str,
    *,
    spec=None,
    lead_days: int = 3,
    timeout: float = 30.0,
) -> Iterator[Signal]:
    """Backtest-grade forecasts: what each run said, at its exact lead time.

    The live forecast endpoint answers with the *current* blend of models,
    which is fine in the present but cannot be replayed: asking it about a
    past date returns a later revision, not what the model actually said. The
    previous-runs API carries ``*_previous_dayN`` values - the run from N days
    earlier - so a report dated at ``end`` is reconstructed from the exact
    information it would have had, with no lookahead. That is what makes the
    archived scenarios measurable rather than merely narratable.

    Each future day is taken from the run that many days ahead of it, so the
    signal's ``(+Nd)`` lead time is the model's real horizon, not a guess.
    """
    weather = spec if spec is not None else load_weather()
    end_ts = _day_end_ts(end)
    stamp = min(end_ts, time.time())
    cutoff = datetime.fromtimestamp(end_ts, UTC).date()
    leads = list(range(1, min(lead_days, weather.horizon_days) + 1))
    if not leads:
        return
    hourly_vars = sorted(
        {
            f"{hourly}_previous_day{lead}"
            for hourly, *_rest in _PREVIOUS_RULES
            for lead in leads
        }
    )
    limits = _thresholds(weather)
    out: list[Signal] = []
    for node in nodes:
        params = {
            "latitude": node.geo[0],
            "longitude": node.geo[1],
            "timezone": "UTC",
            "hourly": ",".join(hourly_vars),
            "start_date": cutoff.isoformat(),
            "end_date": (cutoff + timedelta(days=leads[-1])).isoformat(),
        }
        try:
            payload = _cached_json(OPEN_METEO_PREVIOUS, params, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - one dead node must not stop the run
            log.warning("previous-run forecast failed for %s: %s", node.name, exc)
            continue
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        for lead in leads:
            when = cutoff + timedelta(days=lead)
            day = when.isoformat()
            for hourly_name, how, label, metric, kind, direction in _PREVIOUS_RULES:
                series = hourly.get(f"{hourly_name}_previous_day{lead}") or []
                value = _fold(
                    [v for t, v in zip(times, series) if t.startswith(day)], how
                )
                if value is None:
                    continue
                level = limits[metric]
                crossed = value >= level if direction == "ge" else value <= level
                if not crossed:
                    continue
                out.append(
                    _forecast_signal(
                        node,
                        day=day,
                        lead=lead,
                        label=label,
                        value=value,
                        unit=_UNITS[metric],
                        kind=kind,
                        direction=direction,
                        ts=stamp,
                        basis=f"the run {lead}d earlier (previous runs)",
                    )
                )
    yield from sorted(out, key=lambda s: s.ts)


def openmeteo_river_signals(
    nodes, end: str, *, spec=None, timeout: float = 30.0
) -> Iterator[Signal]:
    """Low-water forecasts from GloFAS river discharge.

    The gate is *relative* to each node's own recent history rather than an
    absolute level, because GloFAS discharge differs by orders of magnitude
    between catchments: one rule therefore serves the Rhine and the Panama
    Canal without per-river calibration. This is the earliest signal available
    for low-water restrictions, which are exactly what the Rhine and Panama
    analog case files describe.
    """
    weather = spec if spec is not None else load_weather()
    end_ts = _day_end_ts(end)
    if not _live_only(end_ts, "GloFAS river discharge"):
        return
    stamp = min(end_ts, time.time())
    cutoff = datetime.fromtimestamp(end_ts, UTC).date()
    out: list[Signal] = []
    for node in nodes:
        if node.kind not in RIVER_NODE_KINDS:
            continue
        params = {
            "latitude": node.geo[0],
            "longitude": node.geo[1],
            "daily": "river_discharge",
            "past_days": weather.river_baseline_days,
            "forecast_days": weather.horizon_days,
        }
        try:
            payload = _cached_json(OPEN_METEO_FLOOD, params, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("river discharge failed for %s: %s", node.name, exc)
            continue
        daily = payload.get("daily") or {}
        days = daily.get("time") or []
        series = daily.get("river_discharge") or []
        baseline = [
            float(value)
            for day, value in zip(days, series)
            if isinstance(value, (int, float))
            and (_day_of(day, cutoff) or cutoff) < cutoff
        ]
        if len(baseline) < _MIN_BASELINE_DAYS:
            continue
        if _percentile(baseline, 50) < weather.river_min_discharge:
            # A flat ~0 series (a canal, a sea, dry ground) passes any
            # percentile test, so gate on the cell actually carrying water.
            log.debug("river: %s carries no water, skipping", node.name)
            continue
        floor = _percentile(baseline, weather.river_low_percentile)
        for day, value in zip(days, series):
            if not isinstance(value, (int, float)):
                continue
            when = _day_of(day, cutoff) or cutoff
            if when < cutoff or float(value) > floor:
                continue  # the baseline is not the forecast
            text = (
                f"forecast: river discharge falling to {float(value):.1f} m3/s "
                f"at {node.name} on {day} (+{(when - cutoff).days}d), the "
                f"{weather.river_low_percentile:.0f}th percentile of the last "
                f"{len(baseline)} days (GloFAS)"
            )
            anchored, entities = anchor_to_supply_node(
                node.geo[0], node.geo[1], text, (node.name.lower(),)
            )
            out.append(
                Signal(
                    ts=stamp,
                    source_type="analyst",
                    source_name="GloFAS",
                    url="https://open-meteo.com/en/docs/flood-api",
                    text=anchored,
                    geo=node.geo,
                    entities=entities,
                    kind_hint="drought",
                    credibility=FORECAST_CREDIBILITY,
                    signal_id=_signal_id(stamp, f"glofas-{node.node_id}", anchored),
                )
            )
            break  # the first low day is the actionable one
    yield from sorted(out, key=lambda s: s.ts)


def _coord(value: str) -> float | None:
    """NHC coordinates: ``29.9N`` -> 29.9, ``43.4W`` -> -43.4."""
    raw = (value or "").strip().upper()
    if len(raw) < 2 or raw[-1] not in "NSEW":
        return None
    try:
        magnitude = float(raw[:-1])
    except ValueError:
        return None
    return -magnitude if raw[-1] in "SW" else magnitude


_NHC_WORD = {"TS": "Tropical Storm", "HU": "Hurricane", "TD": "Tropical Depression"}


def nhc_cyclone_signals(end: str, *, timeout: float = 30.0) -> Iterator[Signal]:
    """Active tropical cyclones from the NHC's keyless storm feed.

    Up earlier than a GDACS alert for the same storm, and named, so the
    storm-name de-dupe folds it into the GDACS/EONET signal for that cyclone
    instead of triple-counting one storm as corroboration.
    """
    end_ts = _day_end_ts(end)
    if not _live_only(end_ts, "NHC active storms"):
        return
    stamp = min(end_ts, time.time())
    payload = _fetch(NHC_STORMS, timeout=timeout).json()
    out: list[Signal] = []
    for storm in payload.get("activeStorms") or ():
        name = (storm.get("name") or "").strip()
        if not name:
            continue
        code = (storm.get("classification") or "").strip().upper()
        word = _NHC_WORD.get(code, "Cyclone")
        position = f"{storm.get('latitude', '?')} {storm.get('longitude', '?')}"
        text = (
            f"{word} {name} active at {position}, {storm.get('intensity', '?')} kt"
            f" ({storm.get('pressure', '?')} hPa)"
        )
        lat = _coord(storm.get("latitude", ""))
        lon = _coord(storm.get("longitude", ""))
        if lat is not None and lon is not None:
            text, entities = anchor_to_supply_node(lat, lon, text, (name.lower(),))
            geo: tuple[float, float] | None = (lat, lon)
        else:
            geo, entities = _enrich_geo_entities(text)
        out.append(
            Signal(
                ts=stamp,
                source_type="govt_advisory",
                source_name="NHC",
                url=storm.get("url") or "https://www.nhc.noaa.gov/",
                text=text,
                geo=geo,
                entities=entities,
                kind_hint="extreme_weather",
                credibility=0.9,
                signal_id=_signal_id(stamp, f"nhc-{storm.get('id', name)}", text),
            )
        )
    yield from sorted(out, key=lambda s: s.ts)


_NWS_HINTS = (
    (r"hurricane|tropical storm|typhoon", "extreme_weather"),
    (r"flash flood|flood", "flood"),
    (r"high wind|wind advisory|extreme wind", "extreme_weather"),
    (r"extreme heat|heat advisory|excessive heat", "extreme_weather"),
    (r"extreme cold|freeze|frost|winter storm|blizzard|ice storm", "extreme_weather"),
    (r"fire weather|red flag", "fire"),
    (r"drought", "drought"),
    (r"dust storm|blowing dust", "extreme_weather"),
)


def _nws_hint(event: str) -> str | None:
    """kind_hint for a US warning, or ``None`` when it is not a hazard we model."""
    low = event.lower()
    for pattern, hint in _NWS_HINTS:
        if re.search(pattern, low):
            return hint
    return None


def _alert_geo(feature: dict) -> tuple[float, float] | None:
    """First vertex of an NWS alert polygon, if it has one."""
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates")
    try:
        if geometry.get("type") == "Polygon":
            lon, lat = coords[0][0]
        elif geometry.get("type") == "MultiPolygon":
            lon, lat = coords[0][0][0]
        else:
            return None
    except (IndexError, TypeError, ValueError):
        return None
    return float(lat), float(lon)


def nws_alert_signals(
    end: str, *, timeout: float = 30.0, limit: int = 60
) -> Iterator[Signal]:
    """Active US weather warnings from the keyless NWS API.

    US-only, so it covers Arizona/Texas fabs and Spruce Pine quartz and says
    nothing about Asia. That asymmetry is a coverage fact the report should
    state, not hide. The API asks only for a descriptive user agent.
    """
    end_ts = _day_end_ts(end)
    if not _live_only(end_ts, "NWS active alerts"):
        return
    stamp = min(end_ts, time.time())
    payload = _fetch(NWS_ALERTS, timeout=timeout).json()
    out: list[Signal] = []
    for feature in payload.get("features") or ():
        if len(out) >= limit:
            break
        props = feature.get("properties") or {}
        event = (props.get("event") or "").strip()
        hint = _nws_hint(event)
        if not event or hint is None:
            continue
        area = (props.get("areaDesc") or "").split(";")[0].strip()
        headline = (props.get("headline") or event).strip()
        text = f"{headline} (NWS {event}, severity {props.get('severity') or 'unknown'})"
        geo = _alert_geo(feature)
        if geo is not None:
            text, entities = anchor_to_supply_node(geo[0], geo[1], text, ())
        else:
            geo, entities = _enrich_geo_entities(f"{area} {text}")
        out.append(
            Signal(
                ts=stamp,
                source_type="govt_advisory",
                source_name="NWS",
                url=props.get("id") or "https://api.weather.gov/",
                text=f"{text} [{area}]" if area else text,
                geo=geo,
                entities=entities,
                kind_hint=hint,
                credibility=0.9,
                signal_id=_signal_id(
                    stamp, f"nws-{props.get('id', event)}", text
                ),
            )
        )
    yield from sorted(out, key=lambda s: s.ts)


def weather_signals(
    nodes=None,
    end: str = "",
    *,
    market_key: str = "semicon",
    spec=None,
    timeout: float = 30.0,
) -> Iterator[Signal]:
    """Run every keyless weather source and de-duplicate the result.

    ``nodes`` defaults to the chokepoints the market actually watches, which
    is what keeps this to a sensible number of requests. One dead source is
    logged and skipped, exactly like the hazard scouts.
    """
    if nodes is None:
        market = load_markets()[market_key]
        nodes = [n for n in load_chokepoints() if n.kind in market.node_kinds]
    scouts = (
        (
            "open-meteo",
            lambda: openmeteo_forecast_signals(nodes, end, spec=spec, timeout=timeout),
        ),
        (
            "glofas",
            lambda: openmeteo_river_signals(nodes, end, spec=spec, timeout=timeout),
        ),
        ("nhc", lambda: nhc_cyclone_signals(end, timeout=timeout)),
        ("nws", lambda: nws_alert_signals(end, timeout=timeout)),
    )
    collected: list[Signal] = []
    for name, scout in scouts:
        try:
            collected.extend(scout())
        except Exception as exc:  # noqa: BLE001 - one dead source must not stop the run
            log.warning("weather source %s failed: %s", name, exc)
    yield from sorted(_dedupe_hazards(collected), key=lambda s: s.ts)
