"""Split a historical event's impact into supply shocks vs demand shocks, fetched live from Wikipedia each run."""
import re
import time

import requests

S = requests.Session()
S.headers["User-Agent"] = "hackathon-risk-agent/0.1 (research prototype)"
WIKI = "https://en.wikipedia.org/w/api.php"

# Per analog id: (Wikipedia search query, focus regex or None). Resolved through Wikipedia search on every run.
# A focus regex is used when there is no dedicated article: only sentences about that event are kept.
WIKI_QUERY = {
    "chichi-1999": ("1999 Jiji earthquake", None),
    "japan-korea-2019": ("Japan South Korea trade dispute 2019", None),
    "tohoku-2011": ("Economic impact 2011 Tōhoku earthquake and tsunami", None),
    "thai-floods-2011": ("2011 Thailand floods", None),
    "meinong-2016": ("2016 southern Taiwan earthquake", None),
    "uri-2021": ("2021 Texas power crisis", None),
    "taiwan-drought-2021": ("Taiwan drought 2021 semiconductor water rationing", r"drought|water"),
    "renesas-fire-2021": ("Renesas Naka factory fire March 2021", r"fire|Naka"),
    "ever-given-2021": ("2021 Suez Canal obstruction", None),
    "ukraine-2022": ("Economic impact of the Russian invasion of Ukraine", None),
    "shanghai-2022": ("2022 Shanghai COVID-19 outbreak", None),
    "pelosi-drills-2022": ("2022 Chinese military exercises around Taiwan", None),
    "us-controls-2022": ("United States New Export Controls on Advanced Computing and Semiconductors to China", None),
    "ga-ge-2023": ("China export controls gallium germanium August 2023", r"export|2023|China"),
    "red-sea-2023": ("Red Sea crisis", None),
    "hualien-2024": ("2024 Hualien earthquake", None),
    "gaemi-2024": ("Typhoon Gaemi", None),
    "helene-2024": ("Hurricane Helene", r"quartz|Spruce Pine|supply|manufactur|plant|shortage|econom"),
    "krathon-2024": ("Typhoon Krathon", None),
}

SUPPLY = re.compile(r"production|output|manufactur\w*|\bfabs?\b|factor(?:y|ies)|plants?\b|facilit\w+|wafers?|"
                    r"supply|supplies|supplier\w*|shortages?|halt\w*|shut\w*|suspend\w*|disrupt\w*|capacity|lost (?:power|electricity)|"
                    r"power outages?|blackouts?|shipping|ports?\b|containers?|freight|delay\w*|rerout\w*|divert\w*|"
                    r"exports?|inventor\w+|raw materials?|neon|quartz|water rationing|logistic\w*|transit", re.I)
DEMAND = re.compile(r"\bdemands?\b(?! (?:of|for compensation))|consumers?|consumption|sales\b|(?<!in )\borders?\b(?! to)|spending|purchas\w+|retail|"
                    r"tourism|tourists?|recession|\bGDP\b|economic (?:growth|slowdown|activity)|slowdown|"
                    r"confidence|boycott\w*|stockpil\w+|panic buying|hoard\w*|imports?\b|revenue|earnings", re.I)
ECON = re.compile(r"econom\w+|industr\w+|compan\w+|market\w*|prices?|billion|million|percent|%|trade|business\w*|"
                  r"sales|production|supply|demand|chips?|semiconductor\w*|exports?|imports?|shipping", re.I)
SEMI = re.compile(r"semiconductor\w*|chips?\b|TSMC|foundr\w+|wafers?|electronics|memory|DRAM|NAND|hard (?:disk|drive)s?|quartz", re.I)
CASUALTY = re.compile(r"killed|deaths?|fatalit\w+|injur\w+|missing persons?|people were|"
                      r"relief|humanitarian|donat\w+|USAID|non-food|warehouses", re.I)  # human toll / aid, not a market shock
MAX_LEN = 230


def _get(params: dict) -> dict:
    """Wikipedia throttles bursts (429); pace requests and honor Retry-After."""
    for attempt in range(6):
        r = S.get(WIKI, params={**params, "format": "json", "maxlag": 5}, timeout=20)
        if r.ok and r.text.startswith("{"):
            time.sleep(1.0)  # stay under the anonymous rate limit
            return r.json()
        time.sleep(float(r.headers.get("retry-after") or 0) or 2 * (attempt + 1))
    r.raise_for_status()
    raise RuntimeError(f"Wikipedia returned non-JSON: {r.text[:120]}")


def _article(query: str) -> tuple[str, str, str] | None:
    """Top search hit + its plain-text body, in one request."""
    pages = _get({"action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": 1,
                  "prop": "extracts", "explaintext": 1, "redirects": 1}).get("query", {}).get("pages", {})
    if not pages:
        return None
    page = next(iter(pages.values()))
    title = page["title"]
    return title, f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}", page.get("extract", "")


def _sentences(text: str) -> list[str]:
    text = re.split(r"\n==+ ?(?:See also|References|Notes|External links|Further reading) ?==+", text)[0]
    out = []
    for para in text.split("\n"):
        if para.startswith("=="):
            continue
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", para):
            s = s.strip()
            if 40 <= len(s) <= 400 and ECON.search(s):
                out.append(s)
    return out


def classify(text: str, focus: str | None = None, k: int = 2) -> dict[str, list[str]]:
    """Score each economic sentence for supply vs demand language; keep the top k of each, in article order."""
    scored = {"supply": [], "demand": []}
    for i, s in enumerate(_sentences(text)):
        if focus and not re.search(focus, s, re.I):
            continue
        if CASUALTY.search(s) and not SEMI.search(s):  # human toll, not an economic shock
            continue
        sup, dem = len(SUPPLY.findall(s)), len(DEMAND.findall(s))
        if sup == dem:
            continue
        bonus = 2 if SEMI.search(s) else 0
        side, score = ("supply", sup) if sup > dem else ("demand", dem)
        scored[side].append((score + bonus, i, s))
    out = {}
    for side, items in scored.items():
        top = sorted(items, key=lambda x: -x[0])[:k]
        out[side] = [s if len(s) <= MAX_LEN else s[:MAX_LEN - 3].rsplit(" ", 1)[0] + "..."
                     for _, _, s in sorted(top, key=lambda x: x[1])]
    return out


_cache: dict[str, dict] = {}  # per run only - every run re-queries


def shocks(analog_id: str) -> dict:
    """{'supply': [...], 'demand': [...], 'source': title, 'url': ...}; empty lists if the lookup fails."""
    if analog_id in _cache:
        return _cache[analog_id]
    result = {"supply": [], "demand": [], "source": None, "url": None}
    try:
        query, focus = WIKI_QUERY.get(analog_id, (analog_id, None))
        art = _article(query)
        if art:
            title, url, text = art
            result.update(classify(text, focus), source=title, url=url)
    except Exception as ex:
        result["error"] = f"{type(ex).__name__}: {ex}"
    _cache[analog_id] = result
    return result


if __name__ == "__main__":
    for aid in WIKI_QUERY:
        r = shocks(aid)
        print(f"\n### {aid} -> {r['source']}")
        for side in ("supply", "demand"):
            for s in r[side]:
                print(f"  [{side[0].upper()}] {s}")
