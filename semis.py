"""Step 2: filter supply-chain events down to ones that could hit semiconductor manufacturing or shipping."""
import re
from collections import defaultdict
from dataclasses import dataclass, field

from collect import Event, haversine_km

# (name, lat, lon, country, region, kind, weight 0-1, why it matters)
SITES = [
    ("Hsinchu Science Park", 24.78, 121.00, "Taiwan", "taiwan", "fab", 1.0, "TSMC HQ + mature/advanced fabs, UMC; next to Taoyuan air-freight hub"),
    ("Southern Taiwan Science Park (Tainan)", 23.11, 120.27, "Taiwan", "taiwan", "fab", 1.0, "TSMC leading-edge 3/5nm fabs"),
    ("Central Taiwan Science Park (Taichung)", 24.21, 120.62, "Taiwan", "taiwan", "fab", 0.9, "TSMC Fab 15, Micron DRAM"),
    ("Kaohsiung", 22.73, 120.30, "Taiwan", "taiwan", "fab", 0.8, "TSMC 2nm, ASE packaging, major container port"),
    ("Kumamoto", 32.88, 130.85, "Japan", "japan", "fab", 0.6, "TSMC/JASM, Sony image sensors"),
    ("Naka (Ibaraki)", 36.47, 140.49, "Japan", "japan", "fab", 0.5, "Renesas automotive MCUs"),
    ("Yokkaichi", 34.97, 136.62, "Japan", "japan", "fab", 0.6, "Kioxia/WD NAND flash"),
    ("Gyeonggi memory cluster", 37.10, 127.20, "South Korea", "korea", "fab", 0.9, "Samsung Pyeongtaek/Hwaseong, SK hynix Icheon DRAM/HBM"),
    ("Shanghai", 31.20, 121.60, "China", "china", "fab", 0.6, "SMIC, Hua Hong; Yangshan container port"),
    ("Wuxi", 31.49, 120.31, "China", "china", "fab", 0.5, "SK hynix DRAM"),
    ("Xi'an", 34.34, 108.94, "China", "china", "fab", 0.5, "Samsung NAND"),
    ("Singapore", 1.37, 103.89, "Singapore", "sea", "fab", 0.6, "GlobalFoundries, Micron NAND; top transshipment port"),
    ("Penang", 5.36, 100.30, "Malaysia", "sea", "packaging", 0.6, "~13% of global assembly/test (Intel, AMD, Infineon)"),
    ("Laguna / Calabarzon", 14.27, 121.13, "Philippines", "sea", "packaging", 0.3, "back-end assembly/test"),
    ("Phoenix / Chandler", 33.30, -111.84, "United States", "us", "fab", 0.5, "Intel Ocotillo, TSMC Arizona"),
    ("Austin / Taylor", 30.40, -97.70, "United States", "us", "fab", 0.4, "Samsung, NXP, Infineon fabs"),
    ("Hillsboro", 45.54, -122.93, "United States", "us", "fab", 0.4, "Intel process R&D"),
    ("Spruce Pine", 35.92, -82.06, "United States", "us", "materials", 0.6, "most of world's high-purity quartz for crucibles"),
    ("Dresden", 51.05, 13.74, "Germany", "europe", "fab", 0.4, "GlobalFoundries, Infineon, Bosch"),
    ("Veldhoven", 51.42, 5.40, "Netherlands", "europe", "equipment", 0.8, "ASML - sole EUV lithography supplier"),
    ("Odesa", 46.48, 30.72, "Ukraine", "europe", "materials", 0.3, "neon gas purification"),
    ("Taiwan Strait", 24.00, 119.50, "Taiwan", "taiwan", "chokepoint", 1.0, "shipping lane for most leading-edge chip output"),
    ("Strait of Malacca", 2.50, 101.50, "Malaysia", "sea", "chokepoint", 0.8, "Asia-Europe/Middle East shipping lane"),
    ("Bab el-Mandeb / Red Sea", 12.60, 43.30, "Yemen", "middle_east", "chokepoint", 0.5, "Asia-Europe route via Suez"),
    ("Suez Canal", 30.50, 32.35, "Egypt", "middle_east", "chokepoint", 0.5, "Asia-Europe route"),
    ("Strait of Hormuz", 26.57, 56.25, "Iran", "middle_east", "chokepoint", 0.6, "Qatar helium (~1/3 of world supply, used in fabs) and Gulf LNG for Asian power grids"),
    ("Panama Canal", 9.10, -79.70, "Panama", "americas", "chokepoint", 0.3, "Asia-US East Coast route"),
    ("Port of Busan", 35.10, 129.04, "South Korea", "korea", "port", 0.5, "Korea's main export port"),
    ("Hong Kong / Shenzhen", 22.50, 114.10, "China", "china", "port", 0.5, "electronics assembly + air/sea freight"),
]

ALERT_FACTOR = {"Red": 1.0, "Orange": 0.6, "Green": 0.3}
RELEVANCE_THRESHOLD = 0.12
COUNTRY_MATCH_MAX_KM = 500  # country-level match only when the event centroid is reasonably close
HAZARD_CATEGORY = {"EQ": "earthquake", "TC": "typhoon", "FL": "flood", "DR": "drought", "WF": "wildfire", "VO": "volcano"}


def impact_radius_km(e: Event) -> float:
    if e.kind == "EQ":  # fabs halt tools at low shaking intensity, so be generous
        return 60 * 2 ** ((e.magnitude or 5) - 4.5)
    return {"TC": 450, "FL": 150, "WF": 60, "VO": 250, "DR": 0}.get(e.kind, 100)


@dataclass
class Assessment:
    event: Event
    relevance: float
    category: str
    region: str
    severity: int                      # 1 minor, 2 moderate, 3 major
    exposed: list[tuple] = field(default_factory=list)   # (site, kind, distance_km, why)
    rationale: list[str] = field(default_factory=list)


def assess_hazard(e: Event) -> Assessment | None:
    radius = impact_radius_km(e)
    alert_f = ALERT_FACTOR.get(e.alert, 0.3)
    best, exposed = 0.0, []
    for name, lat, lon, country, region, kind, weight, why in SITES:
        d = haversine_km(e.lat, e.lon, lat, lon) if e.lat is not None else 1e9
        decay = max(0.0, 1 - d / radius) if radius else 0.0
        # Floods/droughts/storms are often reported at a country centroid: fall back to country match
        if (country in e.countries and e.kind in ("FL", "DR", "TC") and e.alert in ("Orange", "Red")
                and d < COUNTRY_MATCH_MAX_KM):
            decay = max(decay, 0.35)
        score = weight * alert_f * decay
        if score > 0:
            exposed.append((name, kind, round(d), why, region, score))
            best = max(best, score)
    if best < RELEVANCE_THRESHOLD:
        return None
    exposed.sort(key=lambda x: -x[5])
    top_region = exposed[0][4]
    category = HAZARD_CATEGORY.get(e.kind, e.kind)
    if category == "typhoon" and top_region in ("us", "americas"):
        category = "storm"
    if exposed[0][1] in ("chokepoint", "port") and category in ("typhoon", "storm"):
        category = "shipping_chokepoint"
    sev = {"Red": 3, "Orange": 2}.get(e.alert, 1)
    if e.magnitude and e.kind == "EQ":
        sev = max(sev, 3 if e.magnitude >= 7 else 2 if e.magnitude >= 6 else 1)
    rationale = [
        f"{e.source} reports {e.title} (alert level {e.alert}"
        + (f", magnitude {e.magnitude:.1f}" if e.magnitude else "") + ").",
        f"Impact radius used: {radius:.0f} km for this event type" + (" + country-level match" if e.kind in ("FL", "DR", "TC") else "") + ".",
    ] + [f"Exposed: {n} ({k}) at {d} km - {why}. Score {s:.2f}." for n, k, d, why, _, s in exposed[:4]]
    return Assessment(e, round(best, 2), category, top_region, sev,
                      [x[:4] for x in exposed[:6]], rationale)


# ---- news -----------------------------------------------------------------
SEMI_RE = re.compile(r"\b(semiconductors?|chipmakers?|chip (?:plants?|factor(?:y|ies)|supply|shortages?|exports?|makers?)|"
                     r"foundr(?:y|ies)|wafers?|TSMC|SK hynix|Samsung Electronics|Micron|ASML|lithography|gallium|"
                     r"germanium|neon gas|helium|photoresist|memory chips?|advanced chips?)\b", re.I)
SHIP_RE = re.compile(r"\b(Taiwan Strait|Malacca|Red Sea|Suez|Bab el-Mandeb|Panama Canal|Hormuz|Black Sea|"
                     r"container (?:ships?|shipping)|shipping (?:lanes?|routes?|disruption)|port (?:closure|strike)s?|"
                     r"freight|tankers?|cargo ships?|vessels?)\b", re.I)
NOISE_RE = re.compile(r"\b(talent|workforce|skills?|engineers?|jobs|hiring|stocks? to buy|price target|election|"
                      r"football|soccer|celebrity|movie|album)\b", re.I)
# Alarmist headline language: often the first signal, often wrong
ALARM_RE = re.compile(r"\b(crisis|chaos|catastroph\w*|unprecedented|panic\w*|collapse\w*|meltdown|apocalyp\w*|"
                      r"nightmare|brink|all-out|imminent|devastat\w*|shock\w*|state of emergency)\b|!", re.I)
CATEGORY_RE = [  # first match wins
    ("export_control", r"export (?:controls?|bans?|restrictions?|curbs?|licen[cs]es?)|entity list|sanctions?|tariffs?"),
    ("conflict", r"blockade|military drills?|invasion|missiles?|\bwar\b|airstrikes?|mobiliz\w+|escalat\w+|\bcoup\b|troops"),
    ("shipping_chokepoint", r"canal|strait|port (?:closure|strike)s?|strikes? at|blocked|seiz\w+|reopen\w*|rerout\w*|"
                            r"divert\w*|shipping disruption|congestion|Houthis?|attacks? on (?:ships?|vessels?|tankers?)"),
    ("earthquake", r"earthquakes?|quakes?|tremors?"),
    ("typhoon", r"typhoons?|hurricanes?|cyclones?|tropical storms?"),
    ("flood", r"floods?|flooding"),
    ("drought", r"droughts?|water (?:shortage|rationing)"),
    ("fab_fire_outage", r"\bfires?\b|blaze|explosions?|power (?:outages?|cuts?)|blackouts?|outages?|contamination"),
    ("shortage", r"shortages?|supply crunch|tight supply"),
]
REGION_RE = {
    "taiwan": r"Taiwan|Hsinchu|Tainan|Taichung|Kaohsiung|TSMC",
    "japan": r"Japan|Kumamoto|Renesas|Kioxia",
    "korea": r"Korea|Samsung|SK hynix|Busan",
    "china": r"China|Chinese|Beijing|Shanghai|SMIC|South China Sea",
    "sea": r"Malaysia|Singapore|Penang|Vietnam|Philippines|Thailand|Malacca",
    "us": r"\bU\.?S\.?\b|United States|America|Arizona|Texas|Oregon|Intel|Washington",
    "europe": r"Netherlands|Dutch|ASML|Germany|Dresden|\bEU\b|Europe|Russia\w*|Ukrain\w*|Kyiv|Moscow|Black Sea",
    "middle_east": r"Red Sea|Suez|Houthis?|Hormuz|Iran\w*|Yemen\w*|Israel\w*|Gaza|Saudi|Qatar\w*|Gulf",
    "americas": r"Panama",
}
# Wars/closures that never mention chips can still hit them if they are in these regions
GEO_REGIONS = {"taiwan", "china", "korea", "japan", "sea", "middle_east", "europe", "americas"}
GEO_CATEGORIES = {"conflict", "shipping_chokepoint", "export_control"}


def _region(title: str) -> str:
    # The US is named as an actor in lots of foreign news ("US and Iran discuss..."), so it counts half
    hits = {r: len(re.findall(rx, title, re.I)) * (0.5 if r == "us" else 1) for r, rx in REGION_RE.items()}
    return max(hits, key=hits.get) if any(hits.values()) else "global"


def assess_news(news: list[Event]) -> list[Assessment]:
    """Keep articles that are (a) about chips or shipping plus a disruption, or (b) a war / chokepoint closure /
    trade restriction in a chip- or shipping-critical region. Cluster into stories by (category, region)."""
    stories: dict[tuple, list[tuple[Event, str]]] = defaultdict(list)
    for e in news:
        if NOISE_RE.search(e.title):
            continue
        semi, ship = SEMI_RE.search(e.title), SHIP_RE.search(e.title)
        cat = next((c for c, rx in CATEGORY_RE if re.search(rx, e.title, re.I)), None)
        if not cat:
            continue
        region = _region(e.title)
        link = "semis" if semi else "shipping" if ship else "geo" if (cat in GEO_CATEGORIES and region in GEO_REGIONS) else None
        if link:
            stories[(cat, region)].append((e, link))

    out = []
    for (cat, region), pairs in stories.items():
        items = [e for e, _ in pairs]
        links = {l for _, l in pairs}
        link = "semis" if "semis" in links else "shipping" if "shipping" in links else "geo"
        domains = sorted({a["domain"] for e in items for a in e.articles})
        n = len(domains)
        alarmist = [e for e in items if ALARM_RE.search(e.title) or "sensational" in e.articles[0].get("queries", [])]
        calm_domains = {a["domain"] for e in items if e not in alarmist for a in e.articles}
        mostly_alarmist = len(alarmist) > len(items) / 2

        relevance = (0.3 + 0.1 * min(n, 5)) * {"semis": 1.0, "shipping": 0.7, "geo": 0.5}[link]
        if mostly_alarmist and len(calm_domains) < 2:
            relevance *= 0.6  # sensational and not corroborated by sober coverage
        relevance = round(min(0.9, relevance), 2)
        sev = 3 if n >= 10 else 2 if n >= 4 else 1
        if mostly_alarmist:
            sev = min(sev, 2)  # loud headlines alone don't make an event major

        lead = max((e for e in items if e not in alarmist), key=lambda e: e.time, default=max(items, key=lambda e: e.time))
        story = Event(id=f"news-{cat}-{region}", source="GDELT news", kind="NEWS",
                      title=lead.title, time=lead.time, url=lead.url,
                      articles=[a for e in items for a in e.articles])
        why = {"semis": "mentions semiconductors or chip materials directly",
               "shipping": "mentions shipping lanes, ports or chokepoints",
               "geo": f"no chip/shipping mention, but a {cat.replace('_', ' ')} in a chip- or shipping-critical region"}[link]
        rationale = [f"{len(items)} articles from {n} distinct outlets in the last 72h "
                     f"({', '.join(domains[:5])}{'...' if n > 5 else ''}).",
                     f"Classified as '{cat}' in region '{region}' by keyword rules; {why}."]
        if alarmist:
            rationale.append(f"{len(alarmist)} of {len(items)} headlines use alarmist language or very negative tone"
                             + ("; treated as low-confidence and severity capped until sober outlets confirm."
                                if mostly_alarmist else "; the rest is sober coverage, so kept at normal confidence."))
        if n == 1:
            rationale.append("Single-outlet story: unconfirmed until corroborated.")
        out.append(Assessment(story, relevance, cat, region, sev, rationale=rationale))
    return out


def filter_semis(events: list[Event]) -> list[Assessment]:
    hazards = [a for e in events if e.kind != "NEWS" and (a := assess_hazard(e))]
    stories = assess_news([e for e in events if e.kind == "NEWS"])
    return sorted(hazards + stories, key=lambda a: (-a.relevance, -a.severity))
