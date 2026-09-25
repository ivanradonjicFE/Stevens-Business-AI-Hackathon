"""Report scores: cosine relevance (ported from the d-dev branch, scripts/sitrep.py), the Weather Disruption Index,
and event labels like "Typhoon:Saudel-26"."""
import math
import re
from collections import Counter

# ---- cosine relevance (d-dev: _tokenize / _cosine / _cosine_relevance) ----------------------------------
# Market context document for the semiconductor lens, built exactly as in d-dev from markets.yaml:
# watchlist + node kinds + mechanisms + label, enriched with canonical vocabulary per node kind.
SEMICON = {
    "label": "Semiconductor",
    "watchlist": ["NVDA", "TSM", "ASML", "AMD", "MU", "NXPI", "AVGO", "QCOM", "INTC", "TXN", "AMAT", "LRCX", "KLAC",
                  "ON", "STM", "MCHP"],
    "node_kinds": ["fab", "canal", "strait", "port", "material", "osat"],
    "mechanisms": ["canal_blockage", "seismic", "extreme_weather", "fire", "drought", "geopolitical",
                   "labor_disruption", "shipping_anomaly", "materials_shortage", "pandemic"],
}
NODE_VOCAB = {
    "fab": "semiconductor fab chip wafer foundry",
    "osat": "assembly test packaging",
    "material": "neon quartz substrate palladium gas",
    "canal": "canal shipping container vessel transit",
    "strait": "strait shipping tanker vessel transit",
    "port": "port cargo container shipping freight",
}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _cosine(a: Counter, b: Counter) -> float:
    num = sum(a[t] * b.get(t, 0) for t in a)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    return num / (da * db) if da and db else 0.0


def _context() -> Counter:
    terms = SEMICON["watchlist"] + sorted(SEMICON["node_kinds"]) + sorted(SEMICON["mechanisms"]) + SEMICON["label"].split()
    for kind in SEMICON["node_kinds"]:
        terms += NODE_VOCAB.get(kind, "").split()
    return Counter(_tokenize(" ".join(terms)))


CONTEXT = _context()


def cosine_relevance(docs: list[tuple[str, float]]) -> float:
    """docs = [(signal text, credibility)]. Credibility-weighted mean cosine of the 10 most credible signals
    against the semiconductor context doc, x3 and capped at 1 (raw term-frequency cosine is small)."""
    top = sorted(docs, key=lambda d: d[1], reverse=True)[:10]
    if not top:
        return 0.0
    sims = [_cosine(Counter(_tokenize(text)), CONTEXT) for text, _ in top]
    wsum = sum(c for _, c in top) or 1.0
    rel = sum(s * c for s, (_, c) in zip(sims, top)) / wsum
    return round(min(1.0, rel * 3.0), 2)


# ---- Weather Disruption Index (d-dev layout; inputs from this project's feeds) ------------------------
# Severity: intensity 0.4 / radius 0.2 / duration 0.4.  Vulnerability: concentration 0.4 / buffers 0.2 /
# utility dependency 0.4.  Each factor 0-10; WDI = severity x vulnerability (0-100). <30 GREEN, <60 ORANGE, else RED.
VULNERABILITY = {"fab": 0.85, "materials": 0.8, "equipment": 0.8, "packaging": 0.7, "chokepoint": 0.6, "port": 0.55}
ALERT_INTENSITY = {"Red": 9.0, "Orange": 6.0, "Green": 3.0}


def wdi(m: dict) -> dict:
    """m: event metrics (see agents/correlator.py). Returns every factor so the report can show the math."""
    if m.get("wind_kmh"):
        intensity = min(10.0, m["wind_kmh"] / 25)           # ~250 km/h (Cat 5 / super typhoon) = 10
    elif m.get("magnitude"):
        intensity = min(10.0, max(0.0, (m["magnitude"] - 5) * 2.5))  # tsunami source quake: M9 = 10
    elif m.get("alert"):
        intensity = ALERT_INTENSITY.get(m["alert"], 3.0)
    else:
        intensity = m["severity"] * 3.0                    # news-only event
    intensity = max(intensity, ALERT_INTENSITY.get(m.get("alert"), 0.0))
    radius = min(10.0, m.get("radius_km", 0) / 50) if m.get("radius_km") else 5.0
    duration = min(10.0, max(0.5, m.get("duration_h", 12) / 24))   # days, 10+ days = 10
    sev = 0.4 * intensity + 0.2 * radius + 0.4 * duration

    sites = m.get("exposed") or []
    scores = m.get("site_scores") or []
    exposure = min(1.0, sum(scores[:3]))
    if sites and scores and sum(scores):
        vuln = sum(VULNERABILITY.get(s[1], 0.5) * sc for s, sc in zip(sites, scores)) / sum(scores[:len(sites)])
    else:
        vuln = 0.4                                          # unknown assets -> mid prior (as in d-dev)
    concentration = min(10.0, exposure * 10)
    utility = vuln * 10
    buffers = max(0.0, 10.0 - utility)
    vul = 0.4 * concentration + 0.2 * buffers + 0.4 * utility

    index = sev * vul
    band = "GREEN" if index < 30 else "ORANGE" if index < 60 else "RED"
    r = lambda x: round(x, 1)
    return {"wdi": round(index), "band": band, "severity": r(sev), "vulnerability": r(vul),
            "intensity": r(intensity), "radius": r(radius), "duration": r(duration),
            "concentration": r(concentration), "buffers": r(buffers), "utility": r(utility)}


# ---- labels: "<type>:<name>" -----------------------------------------------------------------------------
def _cyclone_type(lat: float | None, lon: float | None, wind_kmh: float | None) -> str:
    if wind_kmh is not None and wind_kmh < 119:
        return "Tropical Storm"
    if lat is None:
        return "Cyclone"
    if lat >= 0 and 100 <= lon <= 180:
        return "Typhoon"                                     # NW Pacific
    if lat >= 0 and (-180 <= lon <= -30):
        return "Hurricane"                                   # Atlantic, E/C Pacific
    return "Cyclone"                                         # N Indian Ocean, Southern Hemisphere


def _title_name(name: str) -> str:
    return "-".join(p.capitalize() for p in name.split("-"))


def event_label(category: str, hazard: dict | None, storm_names: list[str], region: str) -> str:
    """e.g. Typhoon:Saudel-26, Hurricane:Nolo, Tsunami:Vicinity Of Puerto Rico, Hurricane:Polo/Nolo."""
    if hazard:
        title = hazard["title"]
        if hazard["kind"] == "TS":
            place = title.split(" - ", 1)[-1] if " - " in title else title
            return f"Tsunami:{place.strip()}"
        m = re.match(r"(Tropical Cyclone|Tropical Storm|Tropical Depression|Super Typhoon|Typhoon|Hurricane|Cyclone)\s+(.+)", title)
        name = _title_name(m.group(2).strip()) if m else title
        if m and m.group(1) != "Tropical Cyclone":           # EONET already says what it is
            kind = "Typhoon" if m.group(1) == "Super Typhoon" else m.group(1)
        else:
            kind = _cyclone_type(hazard.get("lat"), hazard.get("lon"), hazard.get("intensity"))
        return f"{kind}:{name}"
    kind = {"tsunami": "Tsunami", "storm": "Hurricane"}.get(category)
    if not kind:
        kind = "Hurricane" if region in ("us", "americas") else "Typhoon" if region in (
            "taiwan", "japan", "korea", "china") else "Cyclone"
    names = [n for n, _ in Counter(n.strip().title() for n in storm_names if n.strip()).most_common(2)]
    where = {"us": "US", "sea": "SE Asia", "middle_east": "Middle East"}.get(region, region.replace("_", " ").title())
    return f"{kind}:{'/'.join(names)}" if names else f"{kind}:Unnamed ({where})"
