# Watchtower — System Design & Scoring Reference

For the next agent or engineer picking this up: this document is the
complete map — what exists, why it looks the way it does, what's real,
and what to watch out for.

## 1. What this is

A deterministic, point-in-time (PIT) supply-chain catastrophe
early-warning system. Signals (news wires, vessel tracking, port
advisories, social) are replayed chronologically; a rule-based pipeline
correlates them into events, scores severity, maps chokepoint exposure,
retrieves historical precedents, and emits a situation report (SITREP).

**Demo scope:** weather/natural-hazard events only (sandstorm, drought,
earthquake). Three replay datasets ship in `data/replay/`; six
non-weather datasets are archived in `data/replay_archived/`.

## 2. Repository layout

```
src/watchtower/
  models.py            # Signal, EventCluster, Analog, Severity enum
  config.py            # KIND_BASE_SEVERITY, HINT_TO_MECHANISMS,
                       # SCENARIO_EXCLUSIONS, MarketSpec, loaders
  geo.py               # haversine + helpers
  agents/
    correlator.py      # clusters signals into EventClusters
    severity.py        # 5-component severity rubric -> band
    exposure.py        # chokepoint proximity scoring
    analog.py          # analog retrieval (PIT-date-filtered)
    briefs.py          # Health/Wealth/Insurance lens prose
    orchestrator.py    # the per-tick pipeline driver
    council.py         # 8-role AI council (model + rule fallback)
    llm.py             # provider-agnostic chat completion client
  sources.py           # replay_signals (JSONL) + every live collector:
                       # gdelt, EONET/GDACS/USGS/NOAA hazards, RSS news,
                       # Open-Meteo/GloFAS/NHC/NWS weather
  data/
    chokepoints.yaml   # the exposure graph (26 nodes)
    markets.yaml       # vertical definitions (semicon/auto/energy)
    feeds.yaml         # keyless news registry (publishers + per-market queries)
    weather.yaml       # forecast thresholds, horizon, river-baseline tuning
    analogs/*.yaml     # 26 historical case files
    replay/*.jsonl     # 3 live demo datasets
    replay_archived/   # 6 parked non-weather datasets
  report.py            # markdown situation reports (PIT) + report cache
  relevance.py         # deterministic lexical relevance (market context)
  synthetic.py         # demo feed + historical impact index
  ui/
    app.py             # Textual TUI (layout, render loop, live polling)
    widgets.py         # in-tree Rich chart widgets (no extra deps)
scripts/
  replay.py            # headless scenario replay (alerts + summary)
  sitrep.py            # SITREP generator (markdown + optional PDF)
  preview_tui.py       # headless layout/overflow report for the TUI
  backtest_forecast.py # PIT forecast replay of an archived scenario
tests/                 # pytest suite (162 tests)
tasks/                 # teammate work packages
sitreps/               # generated reports (md + pdf)
```

## 3. The pipeline (per signal tick)

```
signal ──▶ correlator ──▶ EventCluster (geo+entity clustering)
        ──▶ severity    ──▶ 5-component weighted composite -> band
        ──▶ exposure    ──▶ chokepoint criticality x distance decay
        ──▶ analog      ──▶ top-3 precedents (PIT date-filtered)
        ──▶ briefs      ──▶ health/wealth/insurance lens text
        ──▶ alert       ──▶ issued on severity-band crossings
        ──▶ council     ──▶ AI read, new/escalating events only
```

All deterministic. No LLM in the scoring path. Every number on screen
is traceable to a signal field or a case-file value.

The council (`agents/council.py`) sits *above* that line: it re-reads the
cluster's evidence, exposure table and precedents to decide relevance, a
research brief, a market view and a WATCH/WARNING tier. Its model calls
are optional (`llm.py` returns `None` when no key is present) and every
stage then falls back to a rule path, so the pipeline above is unchanged
whether or not a model is reachable.

A free provider key is enough to switch the model path on: `llm.py`
detects `GROQ_API_KEY` or `CEREBRAS_API_KEY`, supplies that provider's
endpoint and a default model, and keeps the key paired with its own
endpoint. Because those tiers are token budgets (Groq's free tier is 8k
tokens/minute while one analysis costs roughly that), the model path is
**rationed**: only events at or above `ELEVATED`, at most two concurrent,
and no more often than every 40s. Rate limits are waited out using the
provider's own hint rather than treated as a failure, and events passed
over are counted as `N rationed` in the strip. The rule fallback costs
nothing, so it is never rationed.

## 4. Scoring — the two composites

### Severity rubric (0–1 -> band)

Computed in `agents/severity.py`. Five components:

| Component    | Weight | Source |
|---|---|---|
| event_type   | 0.20 | worst `kind_hint` in cluster (canal_blockage 0.85, seismic 0.75, casualty 0.7, infrastructure 0.6, flood/drought 0.55, …) |
| proximity    | 0.30 | top chokepoint exposure score (criticality × distance decay, 2000km horizon) |
| corroboration| 0.25 | distinct source types × mean credibility |
| spread       | 0.15 | distinct ~10° geo cells reporting |
| escalation   | 0.10 | signals in trailing 6h |

Bands (`config.SEVERITY_BANDS`): LOW <0.18, GUARDED <0.35,
ELEVATED <0.55, HIGH <0.75, SEVERE ≥0.75.

### Weather Disruption Index (WDI, 0–100)

The headline score in SITREPs, per the Weather Disruption Index spec.
Six named factors; two sub-totals multiplied:

```
severity   = 0.4·intensity + 0.2·radius + 0.4·duration
vulnerability = 0.4·concentration + 0.2·buffers + 0.4·utility_dep
WDI = severity × vulnerability          # 0–100
```

Mapping onto pipeline outputs (each raw 0–10):
- intensity   = `event_type` component ×10
- radius      = `spread` component ×10
- duration    = `escalation` component ×10
- concentration = `exposure` score ×10 (capped 10)
- utility dep = node-kind resilience prior ×10 (`_VULNERABILITY`: fab 0.85, material 0.8, osat 0.7, canal/strait 0.6, port 0.55)
- buffers     = `10 − utility_dep` (thin slack for fragile assets)

Bands: <30 GREEN, <60 ORANGE, else RED.
Observed: Panama drought 37 ORANGE, Suez 44 ORANGE, Turkey quake 66 RED.

### Analog match score (0–1)

`agents/analog.py`: `0.55·mechanism-Jaccard + 0.30·sector-Jaccard +
0.15·geo-proximity`. `HINT_TO_MECHANISMS` (config.py) maps signal
kind_hints to mechanism tags; analog YAMLs carry their own mechanism
tags. **PIT invariant:** `analog.date ≤ event.last_seen` — a replay can
never cite a precedent dated after its own cutoff. This is enforced in
code, not config.

### Relevance (lexical cosine, 0–1)

`src/watchtower/relevance.py` is the single implementation: TF cosine
between each of the credibility-weighted top-10 signals and a
market-context document (watchlist tickers + node kinds + mechanism
vocabulary + per-kind canonical terms, all from `markets.yaml`). A signal
document is its `text` **plus its entities** — dropping entities deflates
the score about threefold, so entity-awareness is part of the definition.
Scaled ×3 for readability. `scripts/sitrep.py` and the root `scoring.py`
port both delegate here, so a printed score is reproducible from live
config.

Validated in `tests/test_relevance.py`: the cosine matches a reference
implementation exactly, is symmetric, is 1 for a document against
itself, returns 0 (never NaN) for empty or disjoint documents, and the
audit breakdown reconciles with the printed value. **Caveat measured on
the replay corpus:** raw values span only 0.008–0.104 (×3 → 0.02–0.31),
the cap is never reached, and a lexical score is blind to exposure — an
M7.8 quake with no industry vocabulary in its text scores ≈0 regardless
of proximity to a fab. Read it as an ordinal lexical-overlap signal, not
a calibrated relevance probability; exposure is what makes an event
material.

**Used by the council as an auditable route.** `agents/council.py` runs
this same function in its relevance filter: a gate at ≥ 0.15 admits an
event whose text overlaps the market vocabulary even with no node in
range, and the score, the matched context tokens and the admitting route
are copied from the *rule verdict* into every `CouncilReport` — never
taken from the model, so the figure in a brief is reproducible with no
API key. The model may widen the filter (admit events) but not narrow
it: an exclusion of a rule-kept event is overridden and the override is
recorded in the report. Covered by `tests/test_council.py`.

### Projected insured impact (USD)

Match-weighted mean of the **documented insured losses** in the top
analogs — no scaling or extrapolation. `_parse_usd` pulls the first
dollar figure from each analog's `quantified_impact`, preferring
insurance-keyed fields (`insurance`, `claims`, `ga`, `loss`,
`liabilit`, `payout`) over trade-flow figures. Weights are printed
per-row (`w=0.46` etc.) so the estimate is fully reconstructible.
Checked against the PCS $25M catastrophe-designation threshold.

## 5. PIT (point-in-time) guarantees

- `scripts/replay.py` and `scripts/sitrep.py` ingest signals in
  timestamp order; nothing downstream sees future signals.
- Analogs are date-filtered against `event.last_seen` — structurally
  impossible to cite the future.
- `SCENARIO_EXCLUSIONS` is belt-and-suspenders per-scenario exclusion
  (the date filter already handles lookahead; the exclusions handle
  same-event self-citation like suez_2021 citing suez_2021).
- The SITREP header states the information cutoff explicitly.
- `kind_hint` values that would fabricate mechanism tags
  (`seismic` on Nord Stream, etc.) are deliberately avoided in data
  authoring.

## 6. Data formats

### Replay JSONL (`data/replay/*.jsonl`) — one signal per line

```json
{"ts":"2021-03-23T05:55:00Z","source_type":"vessel_tracking",
 "source_name":"VesselFinder","url":"https://…","lang":"en",
 "geo":[29.98,32.58],"entities":["ever given","suez canal"],
 "kind_hint":"shipping_anomaly","text":"…","credibility":0.6}
```

`kind_hint` vocabulary (severity-bearing): `shipping_anomaly`,
`canal_blockage`, `infrastructure`, `casualty`, `seismic`, `fire`,
`flood`, `drought`, `pandemic`, `geopolitical`, `labor_disruption`,
`materials_shortage`, `extreme_weather`, plus `advisory`, `market`,
`noise`. A hint must appear in `HINT_TO_MECHANISMS` to tag mechanisms
and in `KIND_BASE_SEVERITY` to carry severity.

Correlator clustering: signals join a cluster if within 150km of the
centroid (or 500km when entities overlap) and within 48h of the
cluster's `last_seen`. Keep datasets inside those envelopes or events
split. ~10–15% far-away noise signals is intentional — they quarantine
into their own clusters.

### Analog YAML (`data/analogs/*.yaml`)

```yaml
id: suez_2021
name: Ever Given Suez Canal blockage
date: 2021-03-23
region: [30.0, 32.55]
report_region: middle_east
mechanisms: [canal_blockage, shipping_delay]
sectors_hit: [container_shipping, energy]
summary: "…"
market_reaction: {index_move: "…", shipping: "…"}
quantified_impact: {insurance: "~$550M GA claim (settled)", …}
lessons: "…"
sources: [{title: …, url: …}]
```

## 7. Commands

```bash
uv run watchtower --scenario panama_drought_2023   # TUI demo
uv run python scripts/replay.py <scenario>         # headless pipeline
uv run python scripts/sitrep.py <scenario> --pdf   # PIT report
uv run pytest                                      # 10 tests
uv run ruff check .
```

`watchtower` CLI: `--scenario` (default suez_2021), `--market`
(semicon/auto/energy), `--speed` (signals/sec; default 1.0).

Live hazard feeds: `uv run python scripts/sitrep.py --source
gdacs|usgs|tsunami|hazard --asof YYYYMMDDHHMMSS [--window-days 3]` —
authoritative disaster feeds (GDACS, USGS, the NOAA tsunami centres),
with `hazard` running them all and de-duplicating.

Live news (keyless): `uv run python scripts/sitrep.py --source news
--asof YYYYMMDDHHMMSS [--market semicon]` — trade-press RSS plus Google
News search, both configured in `data/feeds.yaml`. `--source weather` runs
the forecast lane, `--source all` runs news + weather + hazards.

Live weather (keyless): `--source weather` runs four collectors, each gated so
that only a threshold crossing becomes a signal (thresholds in
`data/weather.yaml`):

- `openmeteo_forecast_signals` — Open-Meteo multi-model daily wind/gust/rain/
  heat/cold per watched node. A cutoff older than two days is answered from the
  *archived* forecast, so a past report cannot read today's run.
- `openmeteo_river_signals` — GloFAS discharge versus each node's own trailing
  92-day baseline (10th percentile). Relative, not absolute, because discharge
  differs by orders of magnitude between catchments. Two gates keep it honest:
  a **median-flow floor** (`river_min_discharge`) rejects the flat ~0 series
  GloFAS returns for canals, seas and dry cells (which pass any percentile
  test), and a minimum baseline length rejects a series too short to judge.
- `nhc_cyclone_signals` — named active cyclones, folded into the GDACS/EONET
  signal for the same storm by the existing storm-name de-dupe.
- `nws_alert_signals` — active US watch/warning polygons (US-only; the report
  states that asymmetry rather than hiding it).

Backtesting: `uv run python scripts/backtest_forecast.py <scenario>
[--previous]` reconstructs the forecast view one day before an archived
event's first signal, from the archived runs (exact PIT) and, with
`--previous`, from `*_previous_dayN` previous-run values so each lead time is
the model's real horizon. GloFAS/NHC/NWS are live-only and cannot be replayed;
the script says so.

Live news (GDELT): `uv run python scripts/sitrep.py --live
"<query>" --asof YYYYMMDDHHMMSS [--window-days 3]` — pulls PIT-bounded
real news, enriches geo/entities via `_GAZETTEER`, classifies
`kind_hint` via `_guess_hint`. **Caveats:** GDELT rate-limits at 1
req/5s and bans shared IPs quickly; its retry path costs over a minute
per query and can still return nothing, which is why the news
collector leaves it **off by default**. Google News RSS is unofficial
and undocumented (its links are redirects, not canonical URLs), so it
is always used alongside other sources, never alone.

All live modes accept `--council-timeout S` (default 90) to bound the
wait for the AI council's brief; the report is written either way.

## 8. Known limitations (be honest with judges)

- Projected loss is a match-weighted mean of documented analog
  losses — a similarity-weighted precedent estimate, not an actuarial
  PML. No severity scaling is applied; every dollar traces to a named
  case file.
- `health` lens briefs are thin for logistics events (the system says
  "indirect" rather than fabricating casualty exposure).
- Vulnerability factors are node-kind priors, not per-asset
  engineering (Chubb-style site assessment is out of scope for a demo).
- GDELT live mode is subject to IP rate limits.
- The council's model path is off unless a key is set (and the `ai` extra
  installed); without it the read is rule-derived and the report says so
  in its `Origin` row.
- On a free tier the council analyses roughly one event a minute: the
  rationing above trades breadth for staying inside the token budget, so
  on a fast synthetic feed most events are passed over (and counted).
- Weather coverage is asymmetric. NHC and NWS are US-only; Open-Meteo and
  GloFAS are global. GloFAS has no keyless history, so the low-water lane
  cannot be backtested.
- Weather thresholds decide whether the lane is signal or noise. They start
  conservative and are meant to be tuned against the archived replays, where
  the ground truth is known. A high threshold means a real event can still be
  missed: the 2021 Suez sandstorm was ~74 km/h, below the 90 km/h gate, and so
  produced no forecast signal in the backtest.

## 9. Extension surface

- Add a scenario: drop a JSONL in `data/replay/` (it auto-appears in
  the TUI dropdown), add a `SCENARIO_EXCLUSIONS` entry if a same-name
  analog exists.
- Add an analog: drop a YAML in `data/analogs/` — the loader schema is
  `_schema.py`; keep `date` honest (it drives the PIT filter).
- Add a chokepoint: append a node in `data/chokepoints.yaml`
  (`kind` ∈ fab/osat/material/canal/strait/port — drives both sector
  matching and the vulnerability prior).
- Add a vertical: a `markets.yaml` entry (watchlist, node_kinds,
  mechanisms) + a Select option — everything downstream is generic.
