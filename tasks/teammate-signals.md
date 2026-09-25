# Task: Suez replay signal dataset

`src/watchtower/data/replay/suez_2021.jsonl` — one JSON object per line.
This IS the demo: the TUI replays these signals at accelerated time and the
system must flag the blockage before "mainstream" signals arrive.

## Schema

```json
{"ts": "2021-03-23T05:49:00Z", "source_type": "vessel_tracking", "source_name": "VesselFinder", "url": "https://...", "lang": "en", "geo": [30.017, 32.580], "entities": ["ever given", "suez canal"], "kind_hint": "shipping_anomaly", "text": "EVER GIVEN (400m ULCS, 20,124 TEU) shown aground across Suez Canal southern channel, blocking both lanes", "credibility": 0.6}
```

Fields:
- `ts` — ISO8601 UTC (event happened ~05:40Z Mar 23, 2021)
- `source_type` — vessel_tracking | social | wire | trade_press | govt_advisory | insurer | analyst
- `credibility` — 0.0–1.0 (social=low, wire/high)
- `kind_hint` — shipping_anomaly | infrastructure | weather | casualty | market | advisory
- `geo` — [lat, lon] where the THING is (Suez ≈ 30.0, 32.6), not the source

## Verified timeline to encode (~45–60 signals)

**Mar 23 (day 0):**
- ~05:40Z: Ever Given runs aground after 40kt gusts/sandstorm, blocks canal
- ~06:00–09:00Z: vessel-tracking services show ship transverse; AIS anomaly
- ~09:00–12:00Z: maritime Twitter/first reports; Egypt starts tug ops
- ~12:00Z: first wires (Reuters/AP); shipping lines monitoring
- PM: GAC/BSM statements; "traffic in both directions halted"
- Evening: oil ticks up; Evergreen confirms

**Mar 24–25 (escalation):**
- SCA suspends navigation; dredging begins; 100+ ships queued
- Lloyd's List: ~$9.6B/day trade held estimate
- Analysts: Asia-Europe delays compound chip/component lead times
- Insurers: GA declared Mar 25 by owners; marine exposure headlines

**Mar 26–28 (persistence):**
- Salvage (Smit/Boskalis); 300+ vessels queued; reroute via Cape talk
- US Navy assist offer; refloat attempts on spring tide

**Mar 29 (resolution):**
- ~13:05Z refloated; backlog-clearing timeline; liability fight begins

## Rules

- Early signals MUST be weak/low-credibility (AIS anomaly, tweets); the
  system should upgrade severity as corroboration arrives. Ordering matters.
- Include ~5 noise signals (unrelated Port of LA congestion note, minor
  Red Sea weather, etc.) so the correlator's filtering is visible.
- Real article URLs where you can verify them; else publisher homepage +
  exact headline text. Never invent a deep link.
- Write a couple of non-English signals (e.g., Arabic Al-Ahram, Japanese
  Nikkei) — multilingual coverage is a rubric point.
