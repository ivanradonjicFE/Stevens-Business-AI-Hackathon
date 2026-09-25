"""Triage agent: decide which new signals matter for the semiconductor supply chain, and how.

Hazards: distance to critical sites is computed by rules (facts), the model adds the impact channel.
News: the model reads every headline (catching events the keyword rules would miss); rules are the fallback."""
import json

import config
import llm
import store
from agents import Agent, to_event
from semis import assess_hazard, classify_headline

CATEGORIES = ["typhoon", "storm", "tsunami", "other"]  # typhoon = any tropical cyclone; storm = US hurricanes
REGIONS = ["taiwan", "japan", "korea", "china", "sea", "us", "europe", "middle_east", "americas", "global"]
CHANNELS = ["fab_production", "materials", "equipment", "packaging", "logistics", "energy", "demand", "policy", "none"]

SYSTEM = """You are the triage analyst in an early-warning system for the SEMICONDUCTOR supply chain
(fabs in Taiwan/Korea/Japan/China/US/EU, packaging in SE Asia, materials like quartz, and the ports, airports and
shipping lanes chips and inputs move through).

SCOPE: only TROPICAL CYCLONES (hurricanes, typhoons, tropical storms) and TSUNAMIS. Anything else - earthquakes
without a tsunami, floods not caused by a cyclone, wars, trade, politics, canals, company news - is NOT relevant.

For each item decide if it signals a real or emerging cyclone/tsunami that could affect semiconductor production
(fab shutdowns, power or water outages), inputs, ports/airports/logistics, or chip demand. A cyclone or tsunami
threatening Taiwan, Japan, Korea, coastal China, SE Asia or US fab regions is relevant even without mentioning chips.
Be strict: storm-name trivia, sports/entertainment cancellations and far-from-industry storms are NOT relevant.
- channel: the main way it would reach the chip industry
- credibility 0-1: how likely this describes a real, current development (wire/major outlet facts high; rumor, opinion,
  clickbait low)
- sensational: true if the headline is alarmist/hyped relative to the facts it states
- storm_name: the storm's name as used in the item (e.g. "Saudel", "Polo"; for a tsunami the place, e.g.
  "Puerto Rico"), or "" if none
- reason: one short sentence"""

SCHEMA = llm.obj({"items": llm.arr(llm.obj({
    "id": llm.STR, "relevant": llm.BOOL, "channel": llm.enum(*CHANNELS), "category": llm.enum(*CATEGORIES),
    "region": llm.enum(*REGIONS), "credibility": llm.NUM, "sensational": llm.BOOL, "storm_name": llm.STR,
    "reason": llm.STR}))})


class TriageAgent(Agent):
    name = "Triage"

    def run(self):
        pending = store.rows("SELECT * FROM signals WHERE triage IS NULL")
        if not pending:
            return 0
        verdicts, to_llm = {}, []
        for s in pending:
            d = s["data"]
            if d["kind"] != "NEWS":
                a = assess_hazard(to_event(d))
                if not a:
                    verdicts[s["id"]] = {"relevant": False, "method": "rules", "reason": "no critical site in impact radius"}
                    continue
                verdicts[s["id"]] = {"relevant": True, "method": "rules", "category": a.category, "region": a.region,
                                     "relevance": a.relevance, "severity": a.severity, "exposed": a.exposed,
                                     "rationale": a.rationale, "credibility": 0.95, "sensational": False,
                                     "site_scores": a.site_scores, "radius_km": a.radius_km,
                                     "channel": "fab_production" if a.exposed[0][1] in ("fab", "packaging") else "logistics"}
                sites = "; ".join(f"{n} ({k}, {dist} km)" for n, k, dist, _ in a.exposed[:3])
                to_llm.append((s["id"], f"HAZARD from {d['source']} ({d['alert']} alert): {d['title']}. Critical sites in impact zone: {sites}"))
            else:
                rule = classify_headline(d["title"])
                verdicts[s["id"]] = ({"relevant": True, "method": "rules", **rule,
                                      "credibility": 0.5, "sensational": rule["alarmist"],
                                      "channel": {"semis": "fab_production", "shipping": "logistics"}.get(rule["link"], "policy")}
                                     if rule else {"relevant": False, "method": "rules", "reason": "no keyword match"})
                dom = d["articles"][0]["domain"] if d.get("articles") else ""
                to_llm.append((s["id"], f"NEWS [{dom}]: {d['title']}"))

        judged = self._llm_triage(to_llm)
        for sid, j in judged.items():
            v = verdicts[sid]
            if v.get("exposed"):   # hazard: keep the spatial facts, add the model's read
                v.update(channel=j["channel"], reason=j["reason"], method="rules+llm")
            else:
                if j["category"] not in ("typhoon", "storm", "tsunami"):  # enforce scope
                    j = {**j, "relevant": False, "reason": f"out of scope: {j['reason']}"}
                elif j["category"] == "typhoon" and j["region"] in ("us", "americas"):
                    j["category"] = "storm"  # Atlantic/E Pacific cyclones = hurricanes, compared with US storms
                verdicts[sid] = {**j, "method": "llm", "rule_view": v if v["relevant"] else None}

        c = store.db()
        for sid, v in verdicts.items():
            c.execute("UPDATE signals SET triage=? WHERE id=?", (json.dumps(v, default=str), sid))
        c.commit()
        kept = sum(v["relevant"] for v in verdicts.values())
        self.log("triaged", f"{len(pending)} signals ({len(judged)} read by {config.FAST_MODEL}) -> {kept} relevant")
        return kept

    def _llm_triage(self, items: list[tuple[str, str]]) -> dict:
        out = {}
        for i in range(0, len(items), config.TRIAGE_BATCH):
            batch = items[i:i + config.TRIAGE_BATCH]
            user = "\n".join(f"{sid} | {text}" for sid, text in batch)
            res = llm.ask_json(SYSTEM, f"Items (id | text):\n{user}", SCHEMA, "triage", model=config.FAST_MODEL, effort="low")
            if not res:
                continue  # rules verdicts stand for this batch
            ids = {sid for sid, _ in batch}
            for it in res["items"]:
                if it["id"] in ids:
                    it["credibility"] = max(0.0, min(1.0, it["credibility"]))
                    out[it["id"]] = it
        return out
