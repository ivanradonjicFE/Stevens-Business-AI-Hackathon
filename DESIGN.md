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
  sources.py           # replay_signals (JSONL) + gdelt_signals (live)
  data/
    chokepoints.yaml   # the exposure graph (26 nodes)
    markets.yaml       # vertical definitions (semicon/auto/energy)
    analogs/*.yaml     # 12 historical case files
    replay/*.jsonl     # 3 live demo datasets
    replay_archived/   # 6 parked non-weather datasets
  ui/app.py            # Textual TUI
scripts/
  replay.py            # headless scenario replay (alerts + summary)
  sitrep.py            # SITREP generator (markdown + optional PDF)
tests/                 # pytest suite (10 tests)
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
```

All deterministic. No LLM in the scoring path. Every number on screen
is traceable to a signal field or a case-file value.

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

### Relevance (cosine similarity, 0–1)

`_cosine_relevance` in `scripts/sitrep.py`: TF cosine between the
credibility-weighted top-10 signal texts and a market-context document
(watchlist tickers + node kinds + mechanism vocabulary + per-kind
canonical terms). Scaled ×3 for readability.

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

Live mode (GDELT): `uv run python scripts/sitrep.py --live
"<query>" --asof YYYYMMDDHHMMSS [--window-days 3]` — pulls PIT-bounded
real news, enriches geo/entities via `_GAZETTEER`, classifies
`kind_hint` via `_guess_hint`. **Caveat:** GDELT rate-limits at 1
req/5s and bans shared IPs quickly; intermittent 0-result calls are a
data-source limitation, not a code bug.

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
