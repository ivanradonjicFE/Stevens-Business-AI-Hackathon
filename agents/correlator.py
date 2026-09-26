"""Correlator agent: merge relevant signals into events, score them, and flag new or escalating ones.

- Each relevant hazard is its own event.
- News attaches to a matching hazard event (same region, compatible type) -> cross-source corroboration;
  otherwise it clusters into a news event by (category, region).
"""
from datetime import datetime, timedelta, timezone

import config
import scoring
import store
from agents import Agent

# News about a storm/tsunami attaches to a hazard-feed event of a compatible type in the same region
COMPATIBLE = {"typhoon": {"typhoon", "storm"}, "storm": {"typhoon", "storm"}, "tsunami": {"tsunami"}}
CHANNEL_WEIGHT = {"fab_production": 1.0, "materials": 1.0, "equipment": 1.0, "packaging": 0.9, "logistics": 0.8,
                  "energy": 0.8, "policy": 0.8, "demand": 0.7, "none": 0.4}


def level_of(relevance: float, severity: int) -> str:
    s = relevance * severity / 3
    return "WARNING" if s >= 0.5 else "WATCH" if s >= 0.2 else "ADVISORY"


def rank(level: str | None) -> int:
    return config.LEVELS.index(level) if level in config.LEVELS else -1


class CorrelatorAgent(Agent):
    name = "Correlator"

    def run(self) -> list[str]:
        new = store.rows("SELECT * FROM signals WHERE event_id IS NULL AND triage IS NOT NULL")
        touched = set()
        c = store.db()
        for s in new:
            t = s["triage"]
            if not t.get("relevant"):
                c.execute("UPDATE signals SET event_id='-' WHERE id=?", (s["id"],))
                continue
            eid = self._event_for(s)
            c.execute("UPDATE signals SET event_id=? WHERE id=?", (eid, s["id"]))
            touched.add(eid)
        c.commit()
        for eid in touched:
            self._rescore(eid)
        closed = self._close_stale()
        if touched or closed:
            self.log("correlated", f"{len(new)} signals -> {len(touched)} events updated, {closed} closed")
        return sorted(touched)

    def _event_for(self, s: dict) -> str:
        t, d = s["triage"], s["data"]
        if d["kind"] != "NEWS":
            return f"hz-{s['id']}"
        hazards = store.rows("SELECT * FROM events WHERE status='active' AND id LIKE 'hz-%'")
        # 1) same storm by name (triage extracts it): "Typhoon Saudel lashes Taiwan" -> Typhoon:Saudel-26
        name = (t.get("storm_name") or "").strip().lower()
        if name:
            for ev in hazards:
                if name in ev["metrics"].get("label", "").lower():
                    return ev["id"]
        # 2) otherwise a compatible hazard in the same region
        for ev in hazards:
            if ev["region"] == t["region"] and t["category"] in COMPATIBLE.get(ev["category"], set()):
                return ev["id"]
        return f"news-{t['category']}-{t['region']}"

    def _rescore(self, eid: str):
        sigs = store.rows("SELECT * FROM signals WHERE event_id=?", (eid,))
        hazards = [s for s in sigs if s["data"]["kind"] != "NEWS"]
        news = [s for s in sigs if s["data"]["kind"] == "NEWS"]
        outlets = {a["domain"] for s in news for a in s["data"].get("articles", [])}
        cred = sum(s["triage"].get("credibility", 0.5) for s in news) / len(news) if news else 0.95
        sens = [s for s in news if s["triage"].get("sensational")]
        calm_outlets = {a["domain"] for s in news if not s["triage"].get("sensational") for a in s["data"].get("articles", [])}

        rel, sev, notes = 0.0, 1, []
        if hazards:
            h = max(hazards, key=lambda s: s["triage"]["relevance"])
            rel, sev = h["triage"]["relevance"], h["triage"]["severity"]
            notes.append(f"Hazard feed: {h['data']['source']} {h['data']['alert']} alert, relevance {rel:.2f} from site proximity.")
        if news:
            chan = max((s["triage"].get("channel", "none") for s in news), key=lambda c: CHANNEL_WEIGHT.get(c, 0.4))
            news_rel = (0.3 + 0.1 * min(len(outlets), 5)) * CHANNEL_WEIGHT.get(chan, 0.4) * (0.6 + 0.4 * cred)
            news_sev = 3 if len(outlets) >= 10 else 2 if len(outlets) >= 4 else 1
            notes.append(f"News: {len(news)} headlines from {len(outlets)} outlets, mean credibility {cred:.2f}, "
                         f"main channel '{chan}'.")
            if hazards:
                rel = min(0.95, max(rel, news_rel) + 0.1)
                notes.append("Hazard feed and news agree: +0.10 corroboration bonus.")
            else:
                rel, sev = news_rel, news_sev
            if len(sens) > len(news) / 2 and len(calm_outlets) < 2:
                rel *= 0.6
                sev = min(sev, 2)
                notes.append(f"{len(sens)}/{len(news)} headlines sensational with <2 sober outlets: relevance x0.6, severity capped.")
        rel = round(rel, 2)

        lead = hazards[0] if hazards else max(news, key=lambda s: (s["triage"].get("credibility", 0), s["data"]["time"]))
        t = lead["triage"]
        old = store.get_event(eid) or {}
        hd = hazards[0]["data"] if hazards else None
        ht = hazards[0]["triage"] if hazards else {}
        # cosine relevance docs: hazard = title + exposed sites and why they matter; news = headline
        docs = []
        for s in sigs:
            text = s["data"]["title"]
            if s["data"]["kind"] != "NEWS":
                text += " " + " ".join(f"{x[0]} {x[1]} {x[3]}" for x in s["triage"].get("exposed", []))
            docs.append((text, s["triage"].get("credibility", 0.5)))
        times = sorted(s["data"]["time"] for s in sigs)
        span_h = (datetime.fromisoformat(times[-1]) - datetime.fromisoformat(times[0])).total_seconds() / 3600
        metrics = {"relevance": rel, "severity": sev, "level": level_of(rel, sev), "outlets": len(outlets),
                   "n_signals": len(sigs), "n_hazard": len(hazards), "n_news": len(news), "credibility": round(cred, 2),
                   "sensational_share": round(len(sens) / len(news), 2) if news else 0.0,
                   "start": min(s["data"]["time"] for s in sigs), "notes": notes,
                   "exposed": ht.get("exposed", []), "site_scores": ht.get("site_scores", []),
                   "radius_km": ht.get("radius_km", 0), "alert": hd["alert"] if hd else None,
                   "lat": hd.get("lat") if hd else None, "lon": hd.get("lon") if hd else None,
                   "wind_kmh": hd.get("intensity") if hd else None, "magnitude": hd.get("magnitude") if hd else None,
                   "duration_h": (hd.get("duration_h") if hd and hd.get("duration_h") else max(span_h, 12)),
                   "cosine_relevance": scoring.cosine_relevance(docs),
                   "label": scoring.event_label(t["category"], hd, [s["triage"].get("storm_name", "") for s in news],
                                                t["region"]),
                   "composite": round(rel * sev / 3, 2),
                   "sources": sorted({s["data"]["source"] if s["data"]["kind"] != "NEWS" else "news" for s in sigs})}
        metrics["wdi"] = scoring.wdi(metrics)
        store.save_event({
            "id": eid, "title": lead["data"]["title"], "category": t["category"], "region": t["region"],
            "first_seen": old.get("first_seen") or store.now(), "updated": store.now(), "status": "active",
            "metrics": metrics, "analyzed_metrics": old.get("analyzed_metrics"), "analysis": old.get("analysis"),
            "notified_level": old.get("notified_level"), "notified_at": old.get("notified_at")})

    def _close_stale(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        n = 0
        for ev in store.rows("SELECT id FROM events WHERE status='active'"):
            last = store.rows("SELECT MAX(last_seen) m FROM signals WHERE event_id=?", (ev["id"],))[0]["m"]
            if last and last < cutoff:
                store.db().execute("UPDATE events SET status='closed' WHERE id=?", (ev["id"],))
                n += 1
        store.db().commit()
        return n


def needs_analysis(ev: dict) -> bool:
    """Deep-research an event if it is WATCH+ and new, or has escalated since the last analysis."""
    m, a = ev["metrics"], ev.get("analyzed_metrics")
    if rank(m["level"]) < rank("WATCH"):
        return False
    if not a:
        return True
    return (rank(m["level"]) > rank(a["level"]) or m["outlets"] >= max(1.5 * a["outlets"], a["outlets"] + 4)
            or m["n_hazard"] > a["n_hazard"])
