"""Step 1: collect current events that could disrupt supply chains, from free sources."""
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

S = requests.Session()
S.headers["User-Agent"] = "hackathon-risk-agent/0.1"

LOOKBACK_DAYS = 10
NOW = datetime.now(timezone.utc)


@dataclass
class Event:
    id: str
    source: str
    kind: str                 # EQ, TC, FL, WF, VO, DR, NEWS
    title: str
    time: datetime
    lat: float | None = None
    lon: float | None = None
    countries: list[str] = field(default_factory=list)
    alert: str = "Green"      # Green / Orange / Red (hazards); news uses corroboration instead
    magnitude: float | None = None
    url: str = ""
    articles: list[dict] = field(default_factory=list)  # news only


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


def gdacs() -> list[Event]:
    """UN/EU Global Disaster Alert and Coordination System: EQ, TC, FL, VO, DR, WF."""
    r = S.get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
              params={"eventlist": "EQ;TC;FL;VO;DR;WF"}, timeout=30)
    events = []
    for f in r.json()["features"]:
        p = f["properties"]
        end = _parse(p["todate"])
        if end < NOW - timedelta(days=LOOKBACK_DAYS):
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        countries = [c.get("countryname", "") for c in p.get("affectedcountries") or []] or [p.get("country", "")]
        sev = p.get("severitydata") or {}
        events.append(Event(
            id=f"gdacs-{p['eventtype']}-{p['eventid']}", source="GDACS", kind=p["eventtype"],
            title=p["name"], time=_parse(p["fromdate"]), lat=lat, lon=lon, countries=[c for c in countries if c],
            alert=p.get("alertlevel", "Green"),
            magnitude=sev.get("severity") if p["eventtype"] == "EQ" else None,
            url=(p.get("url") or {}).get("report", ""),
        ))
    return events


def usgs() -> list[Event]:
    """USGS earthquakes M4.5+ in the past week (fresher and more precise than GDACS for quakes)."""
    r = S.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson", timeout=20)
    events = []
    for f in r.json()["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        mag = p["mag"]
        # USGS PAGER alert is the best severity signal; fall back to magnitude
        alert = {"red": "Red", "orange": "Orange"}.get(p.get("alert") or "", "Orange" if mag >= 6.5 else "Green")
        events.append(Event(
            id=f"usgs-{f['id']}", source="USGS", kind="EQ", title=f"M{mag:.1f} earthquake - {p['place']}",
            time=datetime.fromtimestamp(p["time"] / 1000, timezone.utc), lat=lat, lon=lon,
            alert=alert, magnitude=mag, url=p["url"],
        ))
    return events


def eonet() -> list[Event]:
    """NASA EONET open natural events (storms, wildfires, volcanoes)."""
    kinds = {"severeStorms": "TC", "wildfires": "WF", "volcanoes": "VO", "floods": "FL"}
    r = S.get("https://eonet.gsfc.nasa.gov/api/v3/events", params={"status": "open", "days": LOOKBACK_DAYS}, timeout=30)
    events = []
    for e in r.json()["events"]:
        cat = e["categories"][0]["id"]
        if cat not in kinds:
            continue
        g = e["geometry"][-1]  # latest position
        if g["type"] != "Point":
            continue
        events.append(Event(
            id=f"eonet-{e['id']}", source="NASA EONET", kind=kinds[cat], title=e["title"],
            time=_parse(g["date"]), lat=g["coordinates"][1], lon=g["coordinates"][0],
            url=e.get("link", ""),
        ))
    return events


# News queries. GDELT allows 1 request per 5 seconds and rejects long queries, so keep each one short.
NEWS_QUERIES = {
    "supply chain": '("supply chain disruption" OR "port closure" OR "port strike" OR "shipping disruption" '
                    'OR "export ban" OR "export controls" OR blockade) sourcelang:english',
    "semiconductors": '(semiconductor OR chipmaker OR TSMC OR foundry OR "chip plant" OR wafer) '
                      '(fire OR outage OR earthquake OR typhoon OR shortage OR halt OR suspend OR "export controls" OR blockade) '
                      'sourcelang:english',
    "canals": '("Suez Canal" OR "Panama Canal" OR "Red Sea" OR "Black Sea") '
              '(closed OR blocked OR attack OR suspended OR drought OR diverted) sourcelang:english',
    "straits": '("Strait of Hormuz" OR "Taiwan Strait" OR "Strait of Malacca" OR "Bab el-Mandeb") '
               '(closed OR closure OR blocked OR reopen OR attack OR seized) sourcelang:english',
    "conflict": '(war OR invasion OR airstrike OR "missile strike" OR "military drills" OR blockade OR mobilization OR escalation) '
                '(Taiwan OR China OR Korea OR Iran OR Israel OR Russia OR Ukraine OR Yemen OR "South China Sea") sourcelang:english',
    # Alarmist, very negative-tone coverage: early but unreliable signal, flagged downstream
    "sensational": '(crisis OR chaos OR catastrophic OR unprecedented OR panic OR collapse OR "state of emergency") '
                   '(supply OR shipping OR factory OR port OR exports OR chips OR semiconductor) sourcelang:english tone<-5',
}


CACHE = Path(__file__).parent / ".cache"
CACHE_TTL = 15 * 60  # seconds; re-running within this window reuses results instead of hitting GDELT


class Throttled(Exception):
    pass


def _gdelt(query: str, log=print, allow_fetch: bool = True) -> list[dict]:
    """Fetch one GDELT query, with a 15-minute disk cache. If GDELT throttles us, fall back to the last
    cached result (however old) so a run never stalls on it."""
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"gdelt-{hashlib.sha1(query.encode()).hexdigest()[:12]}.json"
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_TTL:
        return json.loads(f.read_text())
    params = {"query": query, "mode": "artlist", "maxrecords": 75, "format": "json",
              "timespan": "72h", "sort": "datedesc"}
    for attempt in range(2 if allow_fetch else 0):
        r = S.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(8 + 4 * attempt)
            continue
        if not r.text.startswith("{"):  # GDELT reports query errors as plain text
            log(f"    GDELT rejected query: {r.text[:100]}")
            return []
        arts = r.json().get("articles", [])
        f.write_text(json.dumps(arts))
        return arts
    if f.exists():
        age = (time.time() - f.stat().st_mtime) / 60
        log(f"    GDELT throttled; using cached result from {age:.0f} min ago")
        return json.loads(f.read_text())
    log("    GDELT throttled and no cache; skipping this query")
    raise Throttled


def gdelt() -> list[Event]:
    """Global news via GDELT. Each article becomes a NEWS event tagged with the query that found it;
    semis.py clusters them into stories."""
    events, seen = [], {}
    throttled = 0
    for i, (label, q) in enumerate(NEWS_QUERIES.items()):
        if i and throttled < 2:
            time.sleep(6)
        try:
            # After 2 throttled queries in a row, stop hitting GDELT this run (cache only)
            arts = _gdelt(q, allow_fetch=throttled < 2)
            throttled = 0 if arts is not None else throttled
        except Throttled:
            throttled += 1
            continue
        for a in arts:
            key = a["title"].strip().lower()
            if key in seen:  # same headline from several queries: remember every query that found it
                seen[key].articles[0]["queries"].append(label)
                continue
            e = Event(
                id=f"gdelt-{abs(hash(a['url']))}", source="GDELT", kind="NEWS", title=a["title"].strip(),
                time=datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc),
                countries=[a.get("sourcecountry", "")], url=a["url"],
                articles=[{"title": a["title"].strip(), "domain": a["domain"], "url": a["url"], "queries": [label]}],
            )
            seen[key] = e
            events.append(e)
    return events


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _dedupe(events: list[Event]) -> list[Event]:
    """Drop USGS quakes GDACS already has, and EONET storms/fires GDACS already has (by name)."""
    gd = [e for e in events if e.source == "GDACS"]
    out = []
    for e in events:
        if e.source == "USGS" and any(
                g.kind == "EQ" and g.lat is not None and haversine_km(e.lat, e.lon, g.lat, g.lon) < 100
                and abs((e.time - g.time).total_seconds()) < 3 * 86400 for g in gd):
            continue
        if e.source == "NASA EONET" and e.kind == "TC":
            # GDACS names storms "Tropical Cyclone BAVI-26"; EONET says "Typhoon Bavi"
            storm_names = {g.title.split()[-1].split("-")[0].lower() for g in gd if g.kind == "TC"}
            if e.title.split()[-1].lower() in storm_names:
                continue
        out.append(e)
    return out


def collect_all(days: int = LOOKBACK_DAYS, log=print) -> list[Event]:
    global LOOKBACK_DAYS
    LOOKBACK_DAYS = days
    events = []
    # USGS week feed and GDELT 72h window only cover recent days; GDACS covers the full lookback
    for fn in (gdacs, usgs, eonet, gdelt):
        try:
            got = fn()
            log(f"  {fn.__name__:6} {len(got):4} events")
            events += got
        except Exception as ex:  # one dead source must not kill the run
            log(f"  {fn.__name__:6} FAILED: {type(ex).__name__}: {ex}")
    return _dedupe(events)
