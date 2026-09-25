# Task: Analog case library review/enrichment

`src/watchtower/data/analogs/` holds 12 YAML case files — the "historical
reactions" engine. I wrote first drafts; your job is to verify and enrich.

## Schema (every file)

```yaml
id: suez_2021                    # snake_case, matches filename
name: Ever Given Suez Canal blockage
date: 2021-03-23                 # event start
duration_days: 6
region: [29.9, 32.55]            # lat, lon of epicenter
mechanisms: [canal_blockage, shipping_delay, chokepoint]
sectors_hit: [container_shipping, autos, electronics]
summary: >-
  One paragraph. What happened, why it mattered for semiconductor
  supply chains specifically.
market_reaction:
  index_move: "SOX -2.5% over following week"    # or null
  spot_prices: "container rates already elevated, compounded"
  lead_time_delta: "+1-2 wk effective delay for Asia-Europe freight"
quantified_impact:
  trade_held_per_day_usd_bn: 9.6     # Lloyd's List estimate
  general_average_claim_usd_m: 916   # SCA claim, settled ~550
sources:
  - title: Lloyd's List estimate of daily trade held
    url: https://...
lessons: >-
  What an insurer/analyst should take from this event when pattern-
  matching new events. 2-3 sentences.
```

## The 10 files (ranked by recurrence × market impact)

1. `covid_2020` — pandemic fab shutdowns → chip shortage
2. `suez_2021` — canal blockage
3. `tohoku_2011` — quake/tsunami, Renesas + wafer supply
4. `thailand_2011` — floods, HDD/OSAT capacity
5. `texas_uri_2021` — freeze, Austin fabs offline ~1 month
6. `renesas_fire_2021` — N3 fab fire, auto MCUs
7. `taiwan_drought_2021` — fab water supply
8. `ukraine_neon_2022` — neon/palladium supply risk
9. `jiji_1999` — Taiwan quake, DRAM spot +300% in days
10. `westcoast_lockout_2002` — 10-day port shutdown, ~$1B+/day

## Your job (priority order)

1. Verify every quantified number against a real source; fix or delete.
2. Fill real `sources:` URLs (publisher, title, real link — Reuters, BBC,
   Lloyd's List, company filings, USGS, insurers' disclosures).
3. Sharpen `lessons:` — write what a Chubb underwriter would care about.
4. Tighten `mechanisms:` tags — the retriever matches on these.
