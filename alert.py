"""Steps 4-5: extrapolate from historical analogs to the current event, then write the alert."""
import statistics
from datetime import datetime, timezone

from analogs import TICKERS, current_reaction, find_analogs
from impacts import shocks
from semis import Assessment

CATEGORY_LABEL = {
    "earthquake": "Earthquake", "typhoon": "Typhoon / tropical cyclone", "storm": "Hurricane / severe storm",
    "tsunami": "Tsunami",
    "flood": "Flood", "drought": "Drought / water shortage", "wildfire": "Wildfire", "volcano": "Volcanic activity",
    "fab_fire_outage": "Fab fire / power outage", "export_control": "Export controls / trade restrictions",
    "conflict": "Geopolitical / military tension", "shipping_chokepoint": "Shipping chokepoint disruption",
    "logistics_shutdown": "Logistics shutdown", "shortage": "Supply shortage",
}
SEV_LABEL = {1: "minor", 2: "moderate", 3: "major"}


def extrapolate(a: Assessment) -> dict:
    """Weight each analog's semiconductor-index reaction by similarity, scaled by relative severity."""
    analogs = find_analogs(a.category, a.region)
    for an in analogs:  # supply vs demand evidence, fetched live every run
        an["shocks"] = shocks(an["id"])
    rows = []
    for an in analogs:
        sox = an["reaction"].get("^SOX")
        if not sox:
            continue
        scale = max(0.33, min(1.5, a.severity / an["severity"]))  # smaller event -> smaller expected move
        rows.append((an["similarity"], scale, sox))
    proj = {}
    for h in (5, 20):
        if not rows:
            break
        scaled = [s * sox[h] for _, s, sox in rows]
        w = [sim for sim, _, _ in rows]
        proj[h] = {"expected": round(sum(x * y for x, y in zip(scaled, w)) / sum(w), 1),
                   "low": round(min(scaled), 1), "high": round(max(scaled), 1)}
    # Confidence: number of close analogs and whether they agree on direction
    close = sum(1 for sim, _, _ in rows if sim >= 1.0)
    signs = {x[2][20] > 0 for x in rows} if rows else set()
    spread = statistics.pstdev([x[2][20] for x in rows]) if len(rows) > 1 else 99
    confidence = ("high" if close >= 2 and len(signs) == 1 and spread < 8
                  else "medium" if close >= 1 and len(signs) == 1 else "low")

    since = a.event.time.date()
    now = current_reaction(since)
    sox_now = now.get("^SOX", {})
    moved, exp20 = sox_now.get("since_event"), proj.get(20, {}).get("expected")
    if moved is None or exp20 is None:
        read = "Not enough data to compare the current move with history."
    elif abs(exp20) < 1:
        read = "History suggests this type of event has little lasting effect on semiconductor stocks."
    elif abs(moved) > 2 * max(abs(proj[20]["low"]), abs(proj[20]["high"])):
        read = (f"The sector moved {moved:+.1f}%, far beyond anything similar events produced - "
                "other drivers (earnings, macro, policy) likely dominate; don't attribute this move to the event.")
    elif (moved > 0) != (exp20 > 0) and abs(moved) > 1:
        read = "The sector is moving opposite to the historical pattern - other drivers (earnings, macro) likely dominate."
    elif abs(moved) >= abs(exp20):
        read = "The sector has already moved at least as much as history suggests - largely priced in."
    elif abs(moved) < abs(exp20) / 2:
        read = "The sector has moved less than half of what similar events produced - a reaction may still be ahead."
    else:
        read = "The sector is partway through a move consistent with past events."
    return {"analogs": analogs, "projection_sox_excess": proj, "confidence": confidence,
            "current": now, "market_read": read}


def alert_level(a: Assessment) -> str:
    s = a.relevance * a.severity / 3
    return "WARNING" if s >= 0.5 else "WATCH" if s >= 0.2 else "ADVISORY"


def _shock_cell(sentences: list[str]) -> str:
    return " ".join(f"• {t.replace('|', '/')}" for t in sentences) if sentences else "None found in source"


def _pct(v):
    return "n/a" if v is None else f"{v:+.1f}%"


def render(top: list[tuple[Assessment, dict]], n_supply: int, n_semis: int) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# Semiconductor Supply-Chain Alert - {now}", "",
         f"Scanned global disaster feeds and news: **{n_supply}** events with supply-chain relevance, "
         f"**{n_semis}** touching semiconductor manufacturing or shipping. Top {len(top)} below.", ""]
    for i, (a, x) in enumerate(top, 1):
        e = a.event
        L += [f"## {i}. [{alert_level(a)}] {CATEGORY_LABEL.get(a.category, a.category)} - {e.title}", "",
              f"**Source:** {e.source} ({e.time:%Y-%m-%d %H:%M} UTC) - {e.url}  ",
              f"**Relevance to semis:** {a.relevance:.2f} / 1  |  **Severity:** {SEV_LABEL[a.severity]}  |  "
              f"**Region:** {a.region}", ""]
        if a.exposed:
            L += ["**What's exposed**", ""] + [f"- {n} ({k}, {d} km away): {why}" for n, k, d, why in a.exposed[:4]] + [""]

        L += ["**Historical precedents** (semiconductor index vs S&P 500 after the event)", "",
              "| Event | Date | +1 day | +5 days | +20 days | Supply shocks | Demand shocks |",
              "|---|---|---|---|---|---|---|"]
        for an in x["analogs"]:
            sox = an["reaction"].get("^SOX", {})
            sh = an["shocks"]
            supply, demand = _shock_cell(sh["supply"]), _shock_cell(sh["demand"])
            if not sh["supply"] and not sh["demand"]:
                supply = f"No data found in source (curated note: {an['what_happened']})"
            L.append(f"| {an['name']} | {an['date']} | {_pct(sox.get(1))} | {_pct(sox.get(5))} | "
                     f"{_pct(sox.get(20))} | {supply} | {demand} |")
        srcs = [f"{an['name']}: {an['shocks']['url']}" for an in x["analogs"] if an["shocks"].get("url")]
        L += ["", "Supply/demand evidence pulled live from Wikipedia and sorted by keyword rules:", ""] + \
             [f"- {s}" for s in srcs] + [""]

        cur = x["current"]
        L += [f"**Current sector reaction** (vs S&P 500, as of {cur['as_of']})", "",
              "| | Event window (up to 20 trading days) | Last 5 days | Last day |", "|---|---|---|---|"]
        for t in ("^SOX", "TSM", "MU", "ASML", "INTC", "ZIM"):
            if cur.get(t):
                c = cur[t]
                L.append(f"| {TICKERS[t]} | {_pct(c.get('since_event'))} | {_pct(c.get('5d'))} | {_pct(c.get('1d'))} |")
        L.append("")

        p = x["projection_sox_excess"]
        if p:
            L += [f"**Outlook** (confidence: {x['confidence']})", "",
                  f"Similar past events moved semiconductor stocks **{_pct(p[20]['expected'])}** vs the S&P 500 over "
                  f"20 trading days (range {_pct(p[20]['low'])} to {_pct(p[20]['high'])}), adjusted for this event's severity. "
                  f"{x['market_read']}", ""]
        L += ["<details><summary>How we scored this</summary>", ""] + [f"- {r}" for r in a.rationale] + \
             ["- Analogs picked by event type and region similarity; expected move = similarity-weighted average "
              "of each analog's 20-day index reaction, scaled by severity ratio (current / analog, capped 0.33-1.5).",
              "", "</details>", ""]

    L += ["---", "**Caveats:** Automated early-warning signal, not investment or underwriting advice. "
          "Historical reactions include unrelated market moves and a small sample; news-based events may be "
          "unconfirmed. Verify with primary sources (links above) before acting."]
    return "\n".join(L)
