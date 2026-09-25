"""Notifier agent: decide whether an analyzed event deserves a notification, write it (md + pdf + json),
and keep a live status board of every active event.

Report layout follows the d-dev branch SITREP (scripts/sitrep.py): short, table-driven, quantitative -
header metrics, Weather Disruption Index, precedents with match scores, projection, actions, how we scored it."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import charts
import config
import llm
import store
from agents import Agent
from agents.correlator import rank
from analogs import TICKERS
from impacts import DEMAND, SUPPLY
from to_pdf import build as build_pdf

WRITE_SCHEMA = llm.obj({"bottom_line": llm.STR, "what_happened": llm.STR,
                        "actions": llm.arr(llm.obj({"label": llm.STR, "text": llm.STR}))})
WRITE_SYSTEM = """You write the short text parts of a quantitative semiconductor weather-risk situation report.
Readers are investors, risk managers and supply-chain leads. Calm, specific, no hype. Refer to the event by its label
exactly as given (e.g. "Typhoon:Saudel-26").
- bottom_line: ONE sentence, <= 30 words, with the expected SOX move vs S&P 500 from the market view
- what_happened: 1-2 sentences, <= 45 words total, confirmed facts only
- actions: 3-4 items; label is 1-3 words (e.g. "Monitor", "Supplier check", "Hedge review"), text <= 20 words
Use only facts and numbers from the input."""

NOTIFY_DIR = config.OUT / "notifications"
BRIEF_DIR = config.OUT / "briefs"            # analyzed but below the notify threshold
REPORT_TITLE = "# Semiconductor Weather Situation Report"


def _pct(v):
    return "n/a" if v is None else f"{v:+.1f}%"


def _cutoff_header(now: str) -> list[str]:
    return [REPORT_TITLE, "",
            f"**Report type:** Point-in-time (PIT) - {now}  ",
            f"**Information cutoff:** {now}. Reflects only signals received on or before the cutoff.", "", "---", ""]


def _shock_mix(shocks: dict) -> str:
    """Supply vs demand evidence strength in the precedent's source article (keyword hits)."""
    s = sum(len(SUPPLY.findall(x)) for x in shocks.get("supply", []))
    d = sum(len(DEMAND.findall(x)) for x in shocks.get("demand", []))
    if not s and not d:
        return "-"
    lead = "Supply-led" if s >= 1.5 * max(d, 1) else "Demand-led" if d >= 1.5 * max(s, 1) else "Mixed"
    return f"{lead} (S{s}/D{d})"


def _first_sentence(text: str, words: int = 16) -> str:
    s = re.split(r"(?<=\.)\s", text.strip())[0].rstrip(" .;,")
    w = s.split()
    return " ".join(w[:words]) + ("..." if len(w) > words else ".")


def event_header(ev: dict, level: str) -> list[str]:
    """Header block shared by notifications and the run report."""
    m = ev["metrics"]
    lead = f"{m['n_hazard']} hazard feed{'s' if m['n_hazard'] != 1 else ''}" if m["n_hazard"] else "news only"
    return [f"## [{level}] {m['label']} - composite {m['composite']:.2f}", "",
            f"**Lead source:** {', '.join(m['sources'])} ({lead})  ",
            f"**Corroboration:** {m['n_signals']} signals, {m['outlets']} news outlets | **Region:** {ev['region']} | "
            f"**Market lens:** semicon  ",
            f"**Relevance to semicon:** {m['cosine_relevance']:.2f} / 1 (cosine similarity, signal text vs market context)  ",
            f"**Exposure score:** {m['relevance']:.2f} / 1 (distance-decayed site criticality) | "
            f"**Severity:** {m['severity']}/3", ""]


def wdi_table(w: dict) -> list[str]:
    return ["### Weather Disruption Index (WDI)", "", f"**{w['wdi']} / 100 - {w['band']}**", "",
            "| Block | Factor | Raw (0-10) | Weight |", "|---|---|---|---|",
            f"| Severity | intensity | {w['intensity']} | 0.40 |",
            f"| Severity | radius | {w['radius']} | 0.20 |",
            f"| Severity | duration | {w['duration']} | 0.40 |",
            f"| **Severity total** | | **{w['severity']}** | |",
            f"| Vulnerability | concentration | {w['concentration']} | 0.40 |",
            f"| Vulnerability | inventory buffers | {w['buffers']} | 0.20 |",
            f"| Vulnerability | utility dependency | {w['utility']} | 0.40 |",
            f"| **Vulnerability total** | | **{w['vulnerability']}** | |",
            f"| **WDI = severity x vulnerability** | **{w['severity']} x {w['vulnerability']}** | **{w['wdi']}** | - |", ""]


class NotifierAgent(Agent):
    name = "Notifier"

    def run(self, ev: dict, research: dict, view: dict, critique: dict, critiques: list[dict]) -> str | None:
        """Always write the report section (text + charts) for an analyzed event; only NOTIFY when the level is at
        or above the threshold and the event is new or escalated. Held events land in out/briefs/."""
        level = critique["recommended_level"]
        prev = ev.get("notified_level")
        if rank(level) < rank(config.NOTIFY_MIN_LEVEL):
            held = f"level {level} below notify threshold {config.NOTIFY_MIN_LEVEL}"
        elif prev and rank(level) <= rank(prev):
            held = f"already notified at {prev}; no escalation"
        else:
            held = None

        label = ev["metrics"]["label"]
        text = llm.ask_json(WRITE_SYSTEM, json.dumps({"label": label, "level": level, "brief": research["brief"],
                                                      "market_view": view, "critic": critique}, ensure_ascii=False, default=str),
                            WRITE_SCHEMA, "notification", effort="low") or {
            "bottom_line": f"{label}: {view['headline_view']}", "what_happened": research["brief"]["summary"],
            "actions": [{"label": "Monitor", "text": a} for a in view["actionable"][:3]]}
        folder = BRIEF_DIR if held else NOTIFY_DIR
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        safe = re.sub(r"[^A-Za-z0-9-]+", "_", label)
        base = folder / f"{stamp}-{level}-{safe}"
        figs = self._figures(ev, research, view, Path(f"{base}_figs"))
        md = self._render(ev, level, prev, text, research, view, critique, critiques, figs, held)
        base.with_suffix(".md").write_text(md)
        base.with_suffix(".json").write_text(json.dumps(
            {"event": ev, "level": level, "notified": not held, "held_reason": held, "notification": text,
             "research": {k: v for k, v in research.items() if k != "_ext"}, "market_view": view,
             "critiques": critiques, "trace": store.rows(
                "SELECT * FROM agent_log WHERE event_id=? ORDER BY ts", (ev["id"],))}, indent=2, default=str))
        ev["analysis"]["report_md"] = str(base.with_suffix(".md"))
        store.save_event(ev)
        if held:
            self.log("held", f"{held} (brief with charts written: {base.name}.md)", ev["id"])
            return None
        pdf = build_pdf(base.with_suffix(".md"))
        store.db().execute("INSERT INTO notifications VALUES (?,?,?,?)", (store.now(), ev["id"], level, str(pdf)))
        store.db().execute("UPDATE events SET notified_level=?, notified_at=? WHERE id=?", (level, store.now(), ev["id"]))
        store.db().commit()
        self.log("NOTIFIED", f"{label} {level} ({'escalated from ' + prev if prev else 'new'}) -> {pdf.name}", ev["id"])
        return str(pdf)

    def _figures(self, ev: dict, research: dict, view: dict, folder: Path) -> dict:
        """Charts for the report; a failed chart is logged and skipped, never blocks the notification."""
        m, out = ev["metrics"], {}
        jobs = {"pies": lambda p: charts.wdi_pies(m["wdi"], p),
                "map": lambda p: charts.site_map(m, p),
                "projection": lambda p: charts.projection_chart(
                    charts.watch_ticker(view, research["_ext"]["analogs"]), research["_ext"]["analogs"],
                    m["label"], m["severity"], p)}
        for name, job in jobs.items():
            try:
                f = job(folder / f"{name}.png")
                if f:
                    out[name] = f"{folder.name}/{name}.png"   # relative to the notification markdown
            except Exception as ex:
                self.log("chart failed", f"{name}: {type(ex).__name__}: {ex}", ev["id"])
        return out

    def _render(self, ev, level, prev, t, research, view, critique, critiques, figs=None, held=None) -> str:
        figs = figs or {}
        img = lambda k: [f"![]({figs[k]})", ""] if k in figs else []
        brief, ext, m = research["brief"], research["_ext"], ev["metrics"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        mv = view["expected_sox_move"]
        L = _cutoff_header(now) + event_header(ev, level)
        if held:
            L += [f"**Status:** not notified ({held}). Monitoring brief only.", ""]
        L += [f"**Bottom line:** {t['bottom_line']}", "", f"**What happened:** {t['what_happened']}", ""]
        L += wdi_table(m["wdi"]) + img("pies")

        # exposure: top sites in the impact zone
        if m.get("exposed"):
            L += ["### Sites in the impact zone", "", "| Site | Type | Distance | Score |", "|---|---|---|---|"]
            L += [f"| {x[0]} | {x[1]} | {x[2]} km | {sc:.2f} |" for x, sc in zip(m["exposed"][:4], m["site_scores"])] + [""]
        L += img("map")

        # precedents with match scores
        L += ["### Historical precedents", "",
              "| Event | Date | Match | SOX +5d | SOX +20d | Shock mix |", "|---|---|---|---|---|---|"]
        for an in ext["analogs"]:
            sox = an["reaction"].get("^SOX", {})
            L.append(f"| {an['name']} | {an['date']} | {an['similarity'] / 1.5:.2f} | {_pct(sox.get(5))} | "
                     f"{_pct(sox.get(20))} | {_shock_mix(an.get('shocks', {}))} |")
        L += ["", "Match = type and region similarity (1.00 = same type, same region). SOX = semiconductor index vs "
                  "S&P 500. Shock mix = supply vs demand evidence in each precedent's source article.", ""]

        # projection
        p20 = ext["projection_sox_excess"].get(20)
        L += ["### Projected market impact", "",
              f"Expected SOX vs S&P 500 over ~20 trading days: **{mv['base']:+.1f}%** "
              f"(range {mv['low']:+.1f}% to {mv['high']:+.1f}%) | direction **{view['direction']}** | "
              f"confidence **{view['confidence']}**."
              + (f" Rule baseline (match-weighted, severity-scaled): {p20['expected']:+.1f}%." if p20 else ""), ""]
        L += img("projection")
        segs = [s for s in view["segments"] if s["tickers"]][:4]
        if segs:
            L += ["| Segment | Tickers | Direction | Why |", "|---|---|---|---|"]
            L += [f"| {s['segment']} | {', '.join(s['tickers'][:3])} | {s['direction']} | "
                  f"{_first_sentence(s['rationale']).replace('|', '/')} |" for s in segs] + [""]

        cur = ext["current"]
        L += [f"**Market so far** (vs S&P 500, as of {cur['as_of']})", "",
              "| Ticker | Event window (<=20d) | Last 5 days |", "|---|---|---|"]
        for tk in ("^SOX", "TSM", "MU", "ASML", "INTC", "ZIM"):
            if cur.get(tk):
                L.append(f"| {TICKERS[tk]} | {_pct(cur[tk].get('since_event'))} | {_pct(cur[tk].get('5d'))} |")
        L.append("")

        L += ["### Recommended actions", ""] + [f"- **{a['label']}:** {a['text']}" for a in t["actions"][:4]] + [""]

        last = critiques[-1]
        L += ["### How we scored this", "",
              f"- {m['n_signals']} signals ({m['n_hazard']} hazard feed, {m['n_news']} news from {m['outlets']} outlets); "
              f"composite {m['composite']:.2f} = exposure {m['relevance']:.2f} x severity {m['severity']}/3.",
              f"- Relevance = credibility-weighted cosine similarity of the top-10 signals vs the semiconductor market "
              f"context (watchlist, node kinds, mechanisms), x3, capped at 1.",
              "- WDI: severity (wind/alert, impact radius, duration) x vulnerability (site concentration, "
              "buffers, utility dependency per site type).",
              f"- Agents: triage {config.FAST_MODEL}; research, market analysis, critic ({len(critiques)} round"
              f"{'s' if len(critiques) > 1 else ''}, final: {last['verdict']}) and writing {config.DEEP_MODEL}. "
              f"Level set by critic: {level} (rules proposed {m['level']}).",
              "- Caveat: automated early-warning signal, not investment or underwriting advice. Projections are "
              "precedent-derived estimates, not observed outcomes. Full evidence and agent trace in the JSON.", ""]
        return "\n".join(L)


def write_status_board():
    evs = store.rows("SELECT * FROM events WHERE status='active'")
    evs.sort(key=lambda e: (-rank((e.get("analysis") or {}).get("final_level") or e["metrics"]["level"]),
                            -e["metrics"].get("wdi", {}).get("wdi", 0)))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = _cutoff_header(now) + [
        f"**{len(evs)} active events.** Level after critic review where analyzed; rule level otherwise.", "",
        "| Level | Event | Region | Relevance | WDI | Sources | SOX view | Notified |", "|---|---|---|---|---|---|---|---|"]
    for e in evs[:40]:
        m, a = e["metrics"], e.get("analysis") or {}
        lvl = a.get("final_level") or m["level"]
        v = a.get("market_view")
        view = f"{v['expected_sox_move']['base']:+.1f}%" if v else "-"
        w = m.get("wdi", {})
        L.append(f"| {lvl} | {m.get('label', e['title'])[:60]} | {e['region']} | {m.get('cosine_relevance', 0):.2f} | "
                 f"{w.get('wdi', '-')} {w.get('band', '')} | {m['n_hazard']} feed, {m['outlets']} outlets | {view} | "
                 f"{e['notified_level'] or '-'} |")
    config.OUT.mkdir(exist_ok=True)
    (config.OUT / "status.md").write_text("\n".join(L))
    build_pdf(config.OUT / "status.md")  # out/status.pdf: a PDF every cycle, even when nothing new is notified


def write_run_report() -> Path:
    """One PDF for the whole run: status board + the latest report section (notification or monitoring brief,
    with charts) of every analyzed active event, highest level first."""
    parts = [(config.OUT / "status.md").read_text()]
    evs = [e for e in store.rows("SELECT * FROM events WHERE status='active'") if (e.get("analysis") or {}).get("report_md")]
    evs.sort(key=lambda e: (-rank(e["analysis"].get("final_level")), -e["metrics"].get("wdi", {}).get("wdi", 0)))
    for e in evs:
        md = Path(e["analysis"]["report_md"])
        if md.exists():
            body = re.sub(r"!\[(.*?)\]\((?!/)", rf"![\1]({md.parent.name}/", md.read_text())
            if body.startswith(REPORT_TITLE):  # one report title + cutoff for the whole document
                body = body.split("\n---\n", 1)[-1]
            parts.append(body)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    report = config.OUT / f"report-{stamp}.md"
    report.write_text("\n\n<pagebreak>\n\n".join(parts))
    return build_pdf(report)
