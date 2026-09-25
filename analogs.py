"""Step 3: historical semiconductor supply shocks and how markets reacted (computed live from price data)."""
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

# Curated past shocks. Dates are the day the shock hit/was announced; reactions are NOT hardcoded -
# they are measured from market data so every number in an alert is reproducible.
ANALOGS = [
    # (id, date, category, region, severity 1-3, name, what happened)
    ("chichi-1999", "1999-09-21", "earthquake", "taiwan", 3, "Chi-Chi earthquake (M7.6), Taiwan",
     "Island-wide power cuts halted Hsinchu fabs for ~1-2 weeks; DRAM spot prices spiked."),
    ("japan-korea-2019", "2019-07-01", "export_control", "korea", 2, "Japan curbs photoresist/HF exports to Korea",
     "Threatened Samsung/SK hynix materials supply; memory prices firmed."),
    ("tohoku-2011", "2011-03-11", "earthquake", "japan", 3, "Tohoku earthquake & tsunami (M9.0), Japan",
     "Renesas MCU fabs and Shin-Etsu/SUMCO wafer plants offline for months; auto chip shortage."),
    ("thai-floods-2011", "2011-10-10", "flood", "sea", 3, "Thailand industrial-estate floods",
     "Hard-drive and component plants underwater for weeks; HDD prices roughly doubled."),
    ("meinong-2016", "2016-02-06", "earthquake", "taiwan", 2, "Meinong earthquake (M6.4), Tainan",
     "TSMC Tainan fabs lost wafers in process; short production delay."),
    ("uri-2021", "2021-02-15", "storm", "us", 2, "Winter Storm Uri, Texas",
     "Grid failure shut Samsung, NXP and Infineon Austin fabs for weeks; worsened auto chip shortage."),
    ("taiwan-drought-2021", "2021-02-25", "drought", "taiwan", 2, "Taiwan drought / water rationing (approx. start)",
     "Worst drought in ~56 years; fabs trucked in water, production largely maintained."),
    ("renesas-fire-2021", "2021-03-19", "fab_fire_outage", "japan", 2, "Renesas Naka fab fire",
     "Key auto-MCU line down ~1 month; extended automaker shortages."),
    ("ever-given-2021", "2021-03-23", "shipping_chokepoint", "middle_east", 2, "Ever Given blocks Suez Canal",
     "6-day closure; container rates jumped, Asia-Europe schedules slipped for weeks."),
    ("ukraine-2022", "2022-02-24", "conflict", "europe", 2, "Russia invades Ukraine",
     "Ukraine supplied ~half of chip-grade neon; fears over litho gas, broad risk-off."),
    ("shanghai-2022", "2022-03-28", "logistics_shutdown", "china", 2, "Shanghai COVID lockdown",
     "Port and factory disruption for ~2 months; electronics supply chains snarled."),
    ("pelosi-drills-2022", "2022-08-04", "conflict", "taiwan", 2, "PLA drills around Taiwan after Pelosi visit",
     "Live-fire drills near shipping lanes; rerouting, rising Taiwan risk premium."),
    ("us-controls-2022", "2022-10-07", "export_control", "china", 3, "US advanced-chip export controls on China",
     "Restricted AI chips and chipmaking tools to China; equipment makers hit."),
    ("ga-ge-2023", "2023-07-03", "export_control", "china", 2, "China gallium/germanium export controls",
     "Licensing for two chip materials China dominates; prices rose outside China."),
    ("red-sea-2023", "2023-12-15", "shipping_chokepoint", "middle_east", 2, "Red Sea attacks - carriers pause Suez route",
     "Maersk and others diverted around Africa; Asia-Europe transit +10-14 days."),
    ("hualien-2024", "2024-04-03", "earthquake", "taiwan", 2, "Hualien earthquake (M7.4), Taiwan",
     "TSMC evacuated fabs, ~70-80% tool recovery within 10 hours."),
    ("gaemi-2024", "2024-07-24", "typhoon", "taiwan", 1, "Typhoon Gaemi, Taiwan",
     "Markets/offices closed; fabs kept running with limited impact."),
    ("helene-2024", "2024-09-27", "storm", "us", 2, "Hurricane Helene floods Spruce Pine, NC",
     "Quartz mines supplying most high-purity quartz shut for weeks."),
    ("krathon-2024", "2024-10-03", "typhoon", "taiwan", 1, "Typhoon Krathon, Kaohsiung",
     "Southern Taiwan shut for two days; TSMC fabs kept running."),
]

TICKERS = {"^SOX": "PHLX Semiconductor Index", "TSM": "TSMC", "MU": "Micron", "ASML": "ASML",
           "INTC": "Intel", "BDRY": "Dry-bulk shipping ETF", "ZIM": "ZIM container shipping"}
BENCH = "^GSPC"
HORIZONS = (1, 5, 20)  # trading days after the event

# How similar two event categories are, for picking analogs
RELATED = {
    frozenset({"typhoon", "storm"}): 0.7, frozenset({"typhoon", "flood"}): 0.5, frozenset({"storm", "flood"}): 0.5,
    frozenset({"fab_fire_outage", "earthquake"}): 0.4, frozenset({"fab_fire_outage", "storm"}): 0.4,
    frozenset({"conflict", "export_control"}): 0.4, frozenset({"shipping_chokepoint", "logistics_shutdown"}): 0.7,
    frozenset({"shipping_chokepoint", "conflict"}): 0.5, frozenset({"typhoon", "shipping_chokepoint"}): 0.4,
    frozenset({"shortage", "fab_fire_outage"}): 0.4, frozenset({"shortage", "export_control"}): 0.3,
    frozenset({"drought", "flood"}): 0.2,
}


def similarity(cat: str, region: str, analog) -> float:
    c = 1.0 if cat == analog[2] else RELATED.get(frozenset({cat, analog[2]}), 0.0)
    return c * (1.5 if region == analog[3] else 1.0)


_prices: pd.DataFrame | None = None


def prices() -> pd.DataFrame:
    global _prices
    if _prices is None:
        _prices = yf.download(list(TICKERS) + [BENCH], start="1999-06-01", auto_adjust=True,
                              progress=False)["Close"].ffill(limit=3)
    return _prices


def reaction(event_day: str) -> dict:
    """Excess return vs S&P 500 from the last close BEFORE the event to +1/+5/+20 trading days."""
    px = prices()
    d = pd.Timestamp(event_day)
    before = px.index[px.index < d]
    after = px.index[px.index >= d]
    if not len(before) or len(after) < max(HORIZONS):
        return {}
    base = px.loc[before[-1]]
    out = {}
    for t in TICKERS:
        if pd.isna(base[t]):
            continue
        out[t] = {}
        for h in HORIZONS:
            row = px.loc[after[h - 1]]
            ret = row[t] / base[t] - 1
            bench = row[BENCH] / base[BENCH] - 1
            out[t][h] = round(100 * (ret - bench), 1)
    return out


def current_reaction(since: date) -> dict:
    """What the sector has done since the event started, and over the last 1/5 sessions, vs S&P 500."""
    px = prices()
    last = px.index[-1]
    out = {"as_of": str(last.date())}
    windows = {"1d": (px.index[-2], last), "5d": (px.index[-6], last)}
    start = px.index[px.index < pd.Timestamp(since)]
    if len(start):
        # Same window as the analogs: pre-event close to at most +20 trading days, so old events
        # aren't blamed for months of unrelated market moves
        after = px.index[px.index >= pd.Timestamp(since)]
        end = after[min(max(HORIZONS), len(after)) - 1] if len(after) else last
        windows["since_event"] = (start[-1], end)
        out["event_window_days"] = int(len(after[:max(HORIZONS)]))
    for t in TICKERS:
        out[t] = {}
        for name, (ref, end) in windows.items():
            b, n = px.loc[ref], px.loc[end]
            if pd.isna(b[t]) or pd.isna(n[t]):
                continue
            out[t][name] = round(100 * ((n[t] / b[t] - 1) - (n[BENCH] / b[BENCH] - 1)), 1)
    return out


def find_analogs(cat: str, region: str, k: int = 4) -> list[dict]:
    scored = sorted(((similarity(cat, region, a), a) for a in ANALOGS), key=lambda x: -x[0])
    out = []
    for sim, a in scored[:k]:
        if sim <= 0:
            break
        out.append({"id": a[0], "date": a[1], "category": a[2], "region": a[3], "severity": a[4],
                    "name": a[5], "what_happened": a[6], "similarity": round(sim, 2), "reaction": reaction(a[1])})
    return out
