"""Report figures: WDI factor pies, a map of the event and chip sites, and a price projection for the stock to watch.

Colors follow the reference data-viz palette: categorical slots 1-3 (blue, orange, aqua), validated for all pairs
in light mode; neutral inks for text; recessive gray chrome."""
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no GUI: render straight to files
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.collections import PatchCollection  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402

import config  # noqa: E402
from analogs import TICKERS, prices  # noqa: E402
from semis import SITES  # noqa: E402

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8a8984"
GRID, LAND, SURFACE = "#e6e5e0", "#e9e8e3", "#ffffff"
LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_land.geojson"

plt.rcParams.update({
    "font.family": ["Helvetica", "Arial", "DejaVu Sans"], "font.size": 8.5, "text.color": INK,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.titlesize": 9.5, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlecolor": INK,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "legend.frameon": False, "legend.fontsize": 7.5,
})


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


# ---- 1. WDI factor pies -----------------------------------------------------------------------------------
def wdi_pies(w: dict, path: Path) -> Path | None:
    """Two donuts: how much each weighted factor contributes to the severity and vulnerability totals."""
    blocks = [("Severity", w["severity"], [("Intensity", 0.4 * w["intensity"]), ("Radius", 0.2 * w["radius"]),
                                           ("Duration", 0.4 * w["duration"])]),
              ("Vulnerability", w["vulnerability"], [("Concentration", 0.4 * w["concentration"]),
                                                     ("Inventory buffers", 0.2 * w["buffers"]),
                                                     ("Utility dependency", 0.4 * w["utility"])])]
    if not any(total for _, total, _ in blocks):
        return None
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9))
    for ax, (name, total, parts) in zip(axes, blocks):
        parts = [(label, v) for label, v in parts if v > 0]
        vals = [v for _, v in parts]
        wedges, _ = ax.pie(vals, colors=[BLUE, ORANGE, AQUA][:len(vals)], startangle=90, counterclock=False,
                           wedgeprops={"width": 0.38, "edgecolor": SURFACE, "linewidth": 2})
        for wedge, (label, v) in zip(wedges, parts):   # direct labels in ink, never in the slice color
            ang = math.radians((wedge.theta1 + wedge.theta2) / 2)
            x, y = math.cos(ang), math.sin(ang)
            ax.annotate(f"{label}\n{v:.1f} ({v / sum(vals):.0%})", xy=(0.81 * x, 0.81 * y), xytext=(1.28 * x, 1.2 * y),
                        ha="left" if x >= 0 else "right", va="center", fontsize=7.5, color=INK2,
                        arrowprops={"arrowstyle": "-", "color": MUTED, "lw": 0.6})
        ax.text(0, 0.08, f"{total:.1f}", ha="center", va="center", fontsize=15, fontweight="bold", color=INK)
        ax.text(0, -0.2, "of 10", ha="center", va="center", fontsize=7.5, color=INK2)
        ax.set_title(f"{name} total, by weighted factor", pad=14)
        ax.set_aspect("equal")
    fig.subplots_adjust(wspace=0.9)
    return _save(fig, path)


# ---- 2. map -----------------------------------------------------------------------------------------------
def _land() -> list:
    f = config.ROOT / ".cache" / "ne_110m_land.geojson"
    if not f.exists():
        import requests
        f.parent.mkdir(exist_ok=True)
        f.write_text(requests.get(LAND_URL, timeout=30).text)
    polys = []
    for feat in json.loads(f.read_text())["features"]:
        g = feat["geometry"]
        rings = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        polys += [Polygon(r[0], closed=True) for r in rings]
    return polys


def _circle(lat: float, lon: float, km: float, n: int = 120) -> list[tuple]:
    """Points on a great circle of radius km around (lat, lon)."""
    d, p1, l1 = km / 6371, math.radians(lat), math.radians(lon)
    pts = []
    for i in range(n + 1):
        b = 2 * math.pi * i / n
        p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
        l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
        pts.append((math.degrees(l2), math.degrees(p2)))
    return pts


def _draw(ax, m: dict, exposed_names: set) -> list:
    ax.add_collection(PatchCollection(_land(), facecolor=LAND, edgecolor="none", zorder=0))
    other = [s for s in SITES if s[0] not in exposed_names]
    hit = [s for s in SITES if s[0] in exposed_names]
    ax.scatter([s[2] for s in other], [s[1] for s in other], s=10, color=MUTED, zorder=2, label="Other monitored chip site")
    ax.scatter([s[2] for s in hit], [s[1] for s in hit], s=34, color=BLUE, edgecolor=SURFACE, linewidth=1.2,
               zorder=4, label="Chip site in impact zone")
    if m.get("lat") is not None:
        ring = _circle(m["lat"], m["lon"], m.get("radius_km") or 300)
        ax.fill([p[0] for p in ring], [p[1] for p in ring], color=ORANGE, alpha=0.08, zorder=1)
        ax.plot([p[0] for p in ring], [p[1] for p in ring], color=ORANGE, lw=1.2, ls="--", zorder=3,
                label=f"Impact radius ({m.get('radius_km', 0):.0f} km)")
        ax.scatter([m["lon"]], [m["lat"]], s=90, marker="X", color=ORANGE, edgecolor=SURFACE, linewidth=1.2,
                   zorder=5, label=f"{m['label']} position")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color(GRID)
    return hit


def _label_stack(ax, hit: list, x_text: float, y_lo: float, y_hi: float):
    """Site names stacked in a column with leader lines, so clustered sites never overlap."""
    hit = sorted(hit, key=lambda s: -s[1])[:6]
    if not hit:
        return
    step = (y_hi - y_lo) / (len(hit) + 1)
    for i, s in enumerate(hit):
        y = y_hi - step * (i + 1)
        ax.annotate(s[0].split(" (")[0], xy=(s[2], s[1]), xytext=(x_text, y), fontsize=7, color=INK2,
                    va="center", ha="left", zorder=6, annotation_clip=False,
                    arrowprops={"arrowstyle": "-", "color": MUTED, "lw": 0.6, "shrinkA": 1, "shrinkB": 3})
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color(GRID)


def site_map(m: dict, path: Path) -> Path:
    """World view of every monitored chip site + the event, and a zoom on the impact zone."""
    exposed = {x[0] for x in m.get("exposed") or []}
    has_pos = m.get("lat") is not None
    fig = plt.figure(figsize=(7.2, 3.0))
    gs = fig.add_gridspec(1, 2 if has_pos else 1, width_ratios=[2.1, 1] if has_pos else [1], wspace=0.04)
    world = fig.add_subplot(gs[0])
    _draw(world, m, exposed)
    world.set_xlim(-180, 180)
    world.set_ylim(-58, 80)
    world.set_aspect("equal")
    world.set_title("Event and monitored chip sites" if has_pos else "Monitored chip sites (news-only event: no position)")
    world.legend(loc="lower left", ncol=1, handletextpad=0.3, borderaxespad=0.2)
    if has_pos:
        zoom = fig.add_subplot(gs[1])
        hit = _draw(zoom, m, exposed)
        span = max(6.0, (m.get("radius_km") or 300) / 111 * 1.6)
        zoom.set_xlim(m["lon"] - span, m["lon"] + span)
        zoom.set_ylim(m["lat"] - span * 0.8, m["lat"] + span * 0.8)
        _label_stack(zoom, hit, m["lon"] + span * 1.08, m["lat"] - span * 0.8, m["lat"] + span * 0.8)
        zoom.set_aspect("equal")
        zoom.set_title("Impact zone")
        world.indicate_inset_zoom(zoom, edgecolor=INK2, alpha=0.6)
    return _save(fig, path)


# ---- 3. price projection ----------------------------------------------------------------------------------
def watch_ticker(view: dict, analogs: list[dict]) -> str:
    """The stock to watch: first ticker the analyst rates negative that has price history for at least one
    precedent (e.g. ZIM only listed in 2021); else the semiconductor index."""
    px = prices()
    for seg in view.get("segments", []):
        if seg["direction"] == "negative":
            for t in seg["tickers"]:
                if t in TICKERS and any(_path_after(px[t].dropna(), a["date"]) for a in analogs):
                    return t
    return "^SOX"


def _path_after(px: pd.Series, day: str, n: int = 20) -> list[float] | None:
    """Cumulative raw return from the last close before `day` through each of the next n trading days."""
    d = pd.Timestamp(day)
    before, after = px[px.index < d].dropna(), px[px.index >= d].dropna()
    if before.empty or len(after) < n:
        return None
    base = before.iloc[-1]
    return [after.iloc[k] / base - 1 for k in range(n)]


def projection_chart(ticker: str, analogs: list[dict], label: str, severity: int, path: Path,
                     history_days: int = 60) -> Path | None:
    """Recent price history, then today's price carried forward along the closest precedent's percent path;
    shaded band = range across all precedents with price data. Closest = highest match, then severity nearest
    the current event, then most recent."""
    px = prices()[ticker].dropna()
    order = sorted(analogs, key=lambda a: (-a["similarity"], abs(a["severity"] - severity), -int(a["date"][:4])))
    paths = [(a, p) for a in order if (p := _path_after(px, a["date"]))]
    if not paths:
        return None
    hist = px.iloc[-history_days:]
    today, base = hist.index[-1], hist.iloc[-1]
    fut = pd.bdate_range(today + pd.Timedelta(days=1), periods=20)
    top_a, top_p = paths[0]
    proj = [base * (1 + r) for r in top_p]
    lo = [base * (1 + min(p[k] for _, p in paths)) for k in range(20)]
    hi = [base * (1 + max(p[k] for _, p in paths)) for k in range(20)]

    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    ax.plot(hist.index, hist.values, color=BLUE, lw=1.6, label=f"{TICKERS[ticker]} close")
    xs = [today] + list(fut)
    if len(paths) > 1:
        ax.fill_between(xs, [base] + lo, [base] + hi, color=ORANGE, alpha=0.13, lw=0,
                        label=f"Range across {len(paths)} precedents")
    ax.plot(xs, [base] + proj, color=ORANGE, lw=1.8, ls="--",
            label=f"If it repeats {top_a['name'].split(',')[0]} ({top_a['date'][:4]}): {top_p[-1]:+.1%} in 20 days")
    ax.scatter([today], [base], s=28, color=BLUE, edgecolor=SURFACE, linewidth=1.2, zorder=5)
    ax.axvline(today, color=MUTED, lw=0.8, ls=":")
    ax.annotate(f"today ${base:,.2f}", (today, base), xytext=(-6, 10), textcoords="offset points", ha="right",
                fontsize=7.5, color=INK2)
    chg = top_p[-1]
    ax.annotate(f"${proj[-1]:,.2f} ({chg:+.1%})", (fut[-1], proj[-1]), xytext=(4, 0), textcoords="offset points",
                va="center", fontsize=7.5, color=INK, fontweight="bold")
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylabel("Close (USD)" if ticker != "^SOX" else "Index level")
    ax.set_title(f"Stock to watch for {label}: {TICKERS[ticker]} ({ticker.lstrip('^')}), today's price on past-event paths")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.12), ncol=3, handlelength=1.8, columnspacing=1.2)
    ax.margins(x=0.01)
    fig.autofmt_xdate(rotation=0, ha="center")
    return _save(fig, path)
