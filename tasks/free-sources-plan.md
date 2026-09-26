# Plan: free news + weather plugins for the early-detection reports

Status: **shipped** (all four phases). Scope: add **keyless** news and weather
collectors that feed the live sitrep and the dashboard, without breaking the
point-in-time (PIT) guarantees the reports are built on.

## Why

Today the pipeline is fed by replay files and (for reports) three authority
feeds. The one genuinely *early* signal we are missing is a **forecast**: a
typhoon projected near Hsinchu in 72h, or GloFAS discharge on the Rhine
falling toward low-water, is knowable days before it becomes an observed
disruption. News is the other half: an authority feed tells you an event
happened, a wire tells you it *means* something for a supply chain.

Both can be had for free and without an API key, which keeps the demo
network-only — no accounts, no secrets, nothing to expire.

## What already exists (do not rebuild)

`src/watchtower/sources.py`:

| Collector | Source | Key? | Notes |
|---|---|---|---|
| `replay_signals` | JSONL files | – | the demo spine |
| `gdelt_signals` | GDELT DOC 2.1 | no | the only *news* source today; 1 req/5s, bans shared IPs |
| `eonet_signals` | NASA EONET | no | natural events, category → `kind_hint` map |
| `gdacs_signals` | GDACS | no | alert level → credibility prior |
| `usgs_tsunami_signals` | USGS quakes | no | rolling 4.5+ week feed |
| `noaa_tsunami_signals` | NOAA NTWC/PTWC | no | Atom parsing via `xml.etree` |
| `hazard_signals` | all of the above | no | runs the scouts, logs+skips dead ones, de-dupes |

Supporting machinery we should reuse rather than duplicate:

- `_fetch(url, params, timeout)` — **the single network seam**; every hazard
  test monkeypatches it. New collectors must go through it too.
- `anchor_to_supply_node(lat, lon, text, entities)` — ties a hazard to the
  nearest monitored node (≤1500km) and *appends the node name to the signal
  text*, which is what makes the lexical relevance cosine score it. Forecasts
  need exactly this.
- `_dedupe_hazards` — physical-identity dedupe (same quake, same named storm).
- `_GAZETTEER` / `_enrich_geo_entities` — headlines carry no geo; this maps
  place words to (lat, lon, entities).
- `_guess_hint` — keyword → `kind_hint` (already weather-aware).
- `_day_end_ts`, `_signal_id`, `parse_signal` — PIT cutoff, stable ids, schema.

`scripts/sitrep.py` already has the live entry points:
`--source gdelt|eonet|gdacs|usgs|tsunami|hazard`, `--live QUERY`, `--asof`,
`--window-days`, plus (recently added) a bounded `--council-timeout` drain so a
brief lands *before* the report is written.

The dashboard (`ui/app.py`) has **no live feed**: `_feed_options()` lists only
synthetic + replay JSONL, and only the synthetic feed loops.

## Constraints (these are the design)

1. **PIT is non-negotiable.** Every collector takes an information cutoff and
   drops anything newer. A forecast is *legitimately* PIT, but only if it is
   stamped at its **issue time** and its horizon is explicit — otherwise a
   3-day-out projection masquerades as an observation.
2. **One dead feed must not sink a run.** Follow `hazard_signals`: log, skip,
   continue.
3. **Keyless by default.** No new required secrets. A keyed source may be
   added only as an opt-in extra.
4. **No heavy new dependencies.** `xml.etree` + `httpx` already cover RSS/Atom
   and JSON. Do not add `feedparser` for one feed format.
5. **Testable offline.** New collectors must be exercisable with `_fetch`
   monkeypatched; no test may hit the network (see `tests/conftest.py`, which
   also pins the model path off).
6. **The `Signal` schema does not change.** Fields stay
   (`ts, source_type, source_name, url, text, geo, entities, lang,
   kind_hint, credibility, signal_id`). Forecast horizon is carried in the
   *text* (which the relevance cosine already scores) and in `entities`, not in
   a new field — a schema change would invalidate every replay file.
7. **Never invent a deep link** (per `tasks/teammate-signals.md`). Where a
   source only offers a redirect (Google News RSS), store the redirect URL and
   name the publisher in `source_name`.

## Part A — News plugins (keyless)

**A1. Generic RSS/Atom collector** — `rss_signals(url, end, *, source_name,
source_type, credibility, timeout=20.0)`.
Parse RSS 2.0 `<item>` *and* Atom `<entry>` (we already parse Atom for NOAA,
so reuse the namespace handling). Take `title` + `description/summary`, `link`,
`pubDate`/`updated`. Apply the `_day_end_ts`/timestamp cutoff. Then enrich:
`_enrich_geo_entities(text)` for geo+entities, `_guess_hint(text)` for
`kind_hint`. Emit through the same `_signal_id(ts, source_name, text)`.

**A2. `search_feed_url(query, *, locale, when)`** — build a Google News RSS
search URL from a query string. Keyless and query-parametric, which is what
makes it a drop-in analogue of GDELT. Caveat: undocumented and unofficial;
treat as best-effort and never the only news source.

**A3. Config-driven feed registry** — new `src/watchtower/data/feeds.yaml`:

```yaml
# Per-market query templates -> Google News RSS, plus a curated publisher list.
markets:
  semicon:
    queries:
      - "TSMC OR foundry OR wafer capacity"
      - "semiconductor supply shortage OR outage"
      - "chip export controls OR sanctions"
    feeds:                       # explicit publisher RSS, credibility prior
      - {name: gCaptain,          url: "https://gcaptain.com/feed/",          source_type: trade_press, credibility: 0.6}
      - {name: The Loadstar,      url: "https://theloadstar.com/feed/",       source_type: trade_press, credibility: 0.6}
      - {name: SemiEngineering,   url: "https://semiengineering.com/feed/",   source_type: trade_press, credibility: 0.6}
      - {name: Splash247,         url: "https://splash247.com/feed/",         source_type: trade_press, credibility: 0.55}
```

Rationale for `feeds.yaml` rather than extending `markets.yaml`: the market
specs are about *what to watch*; the feed registry is about *where to read it*,
and it will grow independently. Loaded by a new `load_feeds()` in `config.py`
mirroring `load_markets()`.

**A4. `news_signals(market_key, start, end)`** — the news aggregator, the
news-side twin of `hazard_signals`: run GDELT for each market query plus every
registry RSS feed, then de-dupe.

**A5. Extend dedupe to text.** `_dedupe_hazards` keys on physical identity;
news needs *editorial* identity: same normalized URL, or same normalized title
(lowercase, strip punctuation/publisher suffix), or a Google News item whose
title matches a GDELT headline. Generalize into `_dedupe(signals,
key=...)` and keep the hazard-specific rules as one key function.

## Part B — Weather plugins (keyless)

**B1. `openmeteo_forecast_signals(nodes, end, *, horizon_days=3)`** —
Open-Meteo forecast API (free, non-commercial, ~10k calls/day, **no key**).
Fetch a small variable set per node, and **emit a signal only when a threshold
is crossed** — a sub-threshold forecast is noise and would flood the
correlator (which needs `MIN_CLUSTER_SIGNALS=2` anyway).

| Hazard | Variable(s) | Threshold (starting point) | Node kinds |
|---|---|---|---|
| Cyclone/typhoon wind | `wind_speed_10m` max, `wind_gusts_10m` | ≥ 90 km/h sustained | fab, material, osat, port, canal, strait |
| Extreme rainfall | `precipitation_sum` (24h) | ≥ 80 mm | fab, material, port |
| Heat / cold | `temperature_2m` max / min | ≥ 40 °C / ≤ −15 °C | fab, osat |
| River low water | `river_discharge` (GloFAS) | ≤ 30-day p10 | canal, port, material |

Text must state the claim and its basis, e.g.
`forecast: sustained winds 120 km/h at TSMC Hsinchu, ECMWF run 2026-09-25T00Z, +72h`.
Then run the result through `anchor_to_supply_node` so the node name lands in
`text`/`entities` and the relevance cosine can see it.

**B2. Flood/low-water via Open-Meteo Flood API (GloFAS)** —
`openmeteo_river_signals(...)`. This is the *only* early indicator we can get
for Rhine low-water and Panama Canal draft restrictions, both of which are
already analog case files (`rhine_low_water_2022`, `panama_drought_2023`). High
value per line of code.

**B3. NHC tropical cyclone advisories** — keyless XML/JSON; 5-day forecast
cones with intensity. Strictly *earlier* than a GDACS alert, and `_storm_name`
already exists so the existing storm dedupe covers it. Atlantic/E-Pacific only
→ coverage asymmetry (state it in the report, don't hide it).

**B4. NWS `api.weather.gov` alerts** — keyless (requires a descriptive
`User-Agent`). US watch/warning polygons; relevant to Arizona/Texas fabs and
Spruce Pine quartz. Also US-only.

**B5. Forecast credibility + mechanism.** Forecasts are not observations.
Give them a lower prior (≈0.5–0.6 vs 0.9–0.95 for an authority alert) and, if
needed for analog matching, a `forecast` mechanism tag added to
`HINT_TO_MECHANISMS`. Recommended: keep `kind_hint` as the physical kind
(`extreme_weather`, `flood`, `drought`) so the existing severity rubric and
mechanism map apply unchanged, and let the low credibility plus the explicit
"forecast:" text carry the uncertainty.

## Part C — Wiring

**C1. `weather_signals(nodes, end)` and `news_signals(...)`** join
`hazard_signals` as the three top-level live aggregators.

**C2. `scripts/sitrep.py`** — extend `--source` to `news | weather | all`
(keeping the existing values), defaulting the query set and node set from
`--market` via the new config. Keep `--live QUERY` for ad-hoc GDELT.

**C3. Dashboard live feed.** `_feed_options()` gains
`("Live feeds (news + weather + hazards)", "live")`, and the feed pump needs a
poller for the live case:

- `LivePoller` / `live_signals(interval_s, market_key)`: on each poll, run the
  aggregators for a small look-back window, drop anything whose `signal_id` has
  already been yielded (a `seen: set[str]`), sleep, repeat. It never finishes,
  so `_feed_done` stays False and the `--no-loop`/cycle logic is bypassed.
- Cap the look-back (e.g. 6h) and the poll interval (e.g. 60s) so a demo does
  not hammer GDELT (1 req/5s) or Open-Meteo.
- The strip should show the live state. Note the council is rationed to
  ~1 model brief/minute on a free tier, so expect `N rationed` to be visible —
  the UI already surfaces it.

**C4. Report surface — "Early indicators".** The point of the whole exercise:
a section in the sitrep (and the dashboard outlook) listing *forecast* signals
with their horizon and lead time **before** they become events — e.g.
"ECMWF: 120 km/h winds at TSMC Hsinchu, +72h (issued 2026-09-25T00Z)" — above
the observed-event scoring. This is the differentiator versus a
"what happened" feed, and it is cheap: it is a filtered view of the signals
already in the cluster plus their `geo`/horizon text.

## Part D — Tests and verification

- `tests/test_rss_sources.py`: RSS 2.0 **and** Atom parsing, PIT cutoff,
  quote/HTML-entity handling, malformed feed, redirect-URL preservation.
- Threshold tests: a below-threshold forecast yields **no** signal; a
  crossing yields one with the node anchored and a `forecast:` text.
- `_dedupe` tests: same URL, same title across publishers, GDELT/Google News
  overlap.
- Config test: every `feeds.yaml` entry parses and every market has ≥1 query.
- Live smoke (documented, **not** in CI):
  `uv run python scripts/sitrep.py --source all --asof <now> --council-timeout 120`.
- Existing `tests/test_hazard_sources.py` patterns are the template: `_fetch`
  monkeypatched, zero network.

## Risks and honest limitations

- **Google News RSS is unofficial.** URLs can change without notice; links are
  redirects; rate limits are undocumented. Store the redirect URL + publisher
  name, never fabricate a canonical deep link. Treat it as one of several news
  sources, never the sole one.
- **GDELT** is 1 req/5s and bans shared IPs; the existing retry+curl fallback
  stays.
- **Open-Meteo free tier is non-commercial, ~10k calls/day.** Node-scoped
  fetching plus an hourly cache is required; polling a whole market's nodes
  every minute would burn the quota.
- **Forecasts are projections, not facts.** Mitigated by issue-time stamping,
  explicit horizon text, a lower credibility prior, and an "Early indicators"
  section that is visibly separate from observed-event scoring.
- **Coverage asymmetry.** NHC/NWS are US-only; GDACS/EONET/Open-Meteo are
  global. Any US-centric bias must be stated in the report, not hidden.
- **Threshold tuning** decides whether this is signal or noise. Start
  conservative (high thresholds) and tune against the archived replays, where
  we know the ground truth.

## Suggested sequence

1. **Phase 1 (smallest useful slice):** `rss_signals` + `feeds.yaml` +
   `load_feeds()` + `news_signals` + `--source news`, with tests. Run it live.
2. **Phase 2:** Open-Meteo forecast + GloFAS thresholds, node-anchored,
   `--source weather`. Run it live.
3. **Phase 3:** NHC + NWS; `--source all`; the "Early indicators" report
   section; TUI live feed with the dedupe poller.
4. **Phase 4 (rigour):** replay forecasts historically via Open-Meteo *Single
   Runs* (full horizon of one run = exact PIT) / *Previous Runs* (fixed 1–7 day
   lead times) to backtest early detection on the archived scenarios. GFS
   archives start 2021-03-23 — the Suez replay date, so that case is testable.

## Decisions taken

- **Feed flavour:** trade press, logistics-leaning - gCaptain, The Loadstar,
  Splash247, plus SemiEngineering for the chip side. Adding a source is a row
  in `feeds.yaml`; no code change.
- **Forecast lane:** forecasts are **full participants**. They go through the
  same rubric and can form and notify an event on their own once 2+ corroborate
  (which the existing `MIN_CLUSTER_SIGNALS=2` already enforces). The lower
  credibility prior and explicit `forecast: ... +72h` text carry the
  uncertainty; no separate lane is needed.
- **First slice:** Phase 1 (news), done below.

## Phase 1 - done (news)

Implemented: `feeds.yaml` + `load_feeds()` + `rss_signals()` + `news_signals()`
+ `--source news`, with `tests/test_rss_sources.py` (20 tests, fully offline,
0.15s).

Measured live on the free, keyless sources:

| Source | Time | Signals |
|---|---|---|
| gCaptain (RSS) | 0.3s | 12 |
| The Loadstar (RSS) | 0.3s | 10 |
| Splash247 (RSS) | 0.6s | 10 |
| SemiEngineering (RSS) | 1.2s | 10 |
| Google News RSS (per query) | ~0.5s | 100 (capped to 40) |
| **GDELT (per query)** | **63.2s** | **0** |

Two findings changed the design:

1. **GDELT is now off by default** in `news_signals()` (`gdelt=False`). Its
   retry path costs over a minute per query and returned nothing when
   rate-limited, while the RSS sources answer in about a second and yield more
   headlines between them. GDELT stays available as the opt-in extra
   (`--live QUERY`, `gdelt=True`).
2. **A one-shot report must not depend on rationing luck.** The first live run
   produced a brief for whichever event was dispatched first, so the report on
   the *top* event said "No council brief". Added
   `Council.analyse_now(cluster)`: synchronous, bypasses the interval and
   concurrency caps, returns the existing brief if there is one. The sitrep now
   spends its budget on the event the report is actually about. The
   `min_severity` and `enabled` gates still apply.

Also fixed on the way: `_day_end_ts` now accepts the compact
`YYYYMMDDHHMMSS` cutoff GDELT uses, so one cutoff can be threaded through
sources with different conventions.

## Phase 2 - done (weather)

Implemented: `weather.yaml` + `load_weather()` + `openmeteo_forecast_signals`
+ `openmeteo_river_signals` (GloFAS) + `nhc_cyclone_signals` +
`nws_alert_signals` + `weather_signals` + `--source weather`, with
`tests/test_weather_sources.py` (36 tests, fully offline).

Measured live: `weather_signals` = **69 signals in 8.9s** on the first run
(GloFAS 4 -> 1 after the water gate, NHC 5, NWS 60), from 12 river-kind nodes
(2 have no GloFAS data: Odesa neon, Ports of LA/LB). A later run took ~24s;
the endpoint latency varies, which is why the dashboard caches responses for
an hour.

Two bugs found and fixed live:

1. **GloFAS answers for every coordinate.** Canals, seas and dry cells come
   back as a flat ~0 series, which passes *any* percentile test - so the first
   run reported a bogus "low water at Suez Canal". Added a median-flow gate
   (`river_min_discharge`, default 1.0). Measured medians: Suez 0.0, Ceyhan
   0.0, Busan 0.1, Singapore 2.0, Panama 2.1, Spruce Pine 4.2, Shanghai 1689.
2. **Cold wording pointed the wrong way.** The heat/cold rule shared one "up
   to" phrase, so a cold forecast read "extreme cold up to -20 C". A
   `direction` parameter gives heat "up to" and cold "down to".

## Phase 3 - done (NHC + NWS + ``--source all`` + early indicators + TUI feed)

- `weather_signals` joins `news_signals` and `hazard_signals` as the three
  keyless aggregators; `--source all` runs news + weather + hazards (271 PIT
  signals on the first live run: news 201 / weather 66 / hazards 4).
- **Early indicators.** `report.is_forecast` / `early_indicator_rows` and the
  SITREP's `### Early indicators` table render the forecast signals with their
  lead time, above the observed-event scoring, behind an explicit
  "projections are not observations" and US-coverage caveat.

  A per-node forecast rarely forms an event on its own (the correlator wants
  two signals), so scoping the table to the reported event's own signals hid
  the whole lane. `scripts/sitrep.py::_nearest_forecasts` draws from every live
  signal and ranks by distance to the event, so the table actually fills (the
  first live run showed a GloFAS low-water forecast at Port of Shanghai).
- **Dashboard live feed.** `ui/app.py` gained a `live` feed option: a 60s
  poll over a 12h look-back that de-duplicates by `signal_id` and never ends.

Two bugs made the live board show an empty event list, both fixed and now
covered by `tests/test_synthetic_and_ui.py`:

1. **The feed Select emitted `Changed` on mount**, so `_restart_pipeline` ran
   three times as the dashboard mounted (each joining a council pool). Guarded
   against the no-op change.
2. **The live pump held the orchestrator across its ~20s network fetch**, so a
   restart mid-poll left every signal in a pipeline the UI no longer showed.
   `_pump_live` now re-reads `_orch` after the fetch and drops a stale batch.

After the fix, a 55s headless run reports `signals 270 | events 11 | alerts 9
| PEAK HIGH`.

## Phase 4 - done (PIT forecast backtesting)

Implemented: `openmeteo_previous_run_signals` (the previous-runs API,
`*_previous_dayN`, folded from hourly into daily metrics) and
`scripts/backtest_forecast.py`, which reconstructs the forecast view one day
before an archived event's first signal.

- Archive lane = exact PIT (the forecast actually issued for that date).
- `--previous` lane = each day taken from the run that many days ahead of it,
  so `(+Nd)` is the model's real horizon. Verified live on Panama 2023: heat at
  Suez +1d/+2d, heat at Samsung Austin +3d, from the runs 1/2/3 days earlier.
- Honest null results: Suez 2021, Shanghai 2022 and Red Sea 2023 produced **0**
  archived forecast crossings. For Suez that is correct - the sandstorm was ~74
  km/h, below the 90 km/h gate - and it is a reminder that high thresholds
  trade recall for precision.
- Limitation: GloFAS/NHC/NWS are live-only, so the river low-water lane (the
  one that would speak to the Panama/"Rhine analogs) cannot be backtested here.

### Free-tier reality check

After a session of heavy use the council's calls start failing outright
(`8 failed calls` in the strip) because the free tier's per-minute and daily
budgets are spent. The report is still complete - it falls back to the rule
read and says `Origin | rule fallback (no model reachable)` - which is exactly
what the fallback is for. Model output is a bonus on this budget, not a
precondition.
