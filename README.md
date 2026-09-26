# Watchtower — semiconductor supply-shock early warning

Chubb Challenge 6 (Stevens Business + AI Hackathon). A multi-agent
research pipeline that correlates weak public signals into candidate
events, scores severity with an auditable rubric, measures exposure
against a semiconductor chokepoint map, retrieves historical analogs,
and emits Health / Wealth / Insurance impact briefs in a terminal UI.

## Run it

```bash
uv sync
uv run watchtower                      # TUI demo (synthetic live feed)
uv run watchtower --feed suez_2021     # replay a curated scenario
uv run python scripts/preview_tui.py   # headless layout/chart report
uv run python scripts/replay.py        # headless replay (smoke test)
uv run pytest                          # tests
```

TUI keys: `space` pause/resume · `enter` event audit view (the report
scrolls with `↑`/`↓` and `pgup`/`pgdn`) · `R` full situation report ·
`r` restart · `q` quit · market + feed dropdowns switch verticals and
between the synthetic live feed and the curated historical replays.

### Dashboard

Centered, responsive layout that adapts down to ~76 columns (optional
panels drop, then columns stack). Charts are built in-tree on Rich
renderables — no extra dependencies:

- **severity trajectory** — per-event escalation sparklines
- **exposure** / **analog match** — bars
- **early indicators** — the nearest weather/hazard forecasts for the
  selected event, each with the lead time the model issued it at
  (`+3d`), ranked by distance to the event
- **signal flow** / **level mix** — feed volume histogram and the
  severity split across active events
- **Then vs now** panel — the matched historical case file against the
  live event (match %, duration vs elapsed, market reaction)

`src/watchtower/synthetic.py` generates the demo feed from the real
chokepoint map and analog mechanism vocabulary, so synthetic storylines
cluster, escalate, and match case files exactly like live signals.

### AI agent council

On top of the deterministic pipeline the dashboard runs an eight-role
agent council: hazard/news scouts → relevance + credibility filter →
research → market analyst ⇄ critic → notifier. Only **new or escalating**
events are analysed, and only **WATCH/WARNING** tiers notify.

```bash
uv sync --extra ai                    # installs the openai client (optional)
uv run watchtower
```

One credential is all the configuration needed. A **free provider key** is
detected automatically and brings its own endpoint and default model:

```bash
export GROQ_API_KEY=gsk_...     # https://api.groq.com/openai/v1
# or: CEREBRAS_API_KEY         # https://api.cerebras.ai/v1
```

Anything OpenAI-compatible works too - `OPENAI_API_KEY` (or `.env` /
`Open_AI_API_Key.txt`) plus, for a local model, `OPENAI_BASE_URL`
(e.g. `http://localhost:11434/v1` for Ollama) and `WATCHTOWER_MODEL`.
The key and the endpoint always come from the same source, so a Groq key is
never sent to the OpenAI API.

With no key the council still runs: every agent falls back to a rule path
(exposure table + analog library + evidence list) and the KPI strip reads
`AI off (no API key)`, so the demo never depends on the network.
The strip always shows calls/tokens/briefs, each event row carries the
council tier, and the outlook panel plus every situation report carry the
full read. The relevance figure in a brief is **not** model-generated: it
comes from `src/watchtower/relevance.py` and is reproducible from
`markets.yaml`.

With a model live the council **rations itself**, because a free tier is a
token budget: one full analysis costs roughly 8k tokens (four stages, each
re-sending its context) while Groq's free tier allows 8k tokens/minute. So
the model path only analyses events at or above `ELEVATED`, runs at most two
at once, and starts no more often than every 40s - rather than queueing a
backlog it cannot afford. Rate limits are treated as "wait your turn": the
provider's own retry hint is honoured and the call is retried, so a throttled
run still produces model briefs instead of quietly falling back. Events the
rationing passed over are counted in the strip as `N rationed`. The rule
fallback is free and is deliberately **not** rationed - with no key every
event still gets a read, inline.

A free tier is also a **daily** budget, not just a per-minute one: Groq
allows ~200k tokens/day, and one full analysis costs ~8-11k, so expect
roughly a couple of dozen model-assisted reads per day. Past that the
council keeps working - it says `N failed calls` in the strip and writes a
rule-derived read with `Origin | rule fallback (no model reachable)`. Plan
the demo around a handful of model briefs, not all of them.

### Live feeds (keyless)

Every feed needs **no account and no API key** - news, weather and hazards:

```bash
# PIT news from trade press + Google News search (config: data/feeds.yaml)
uv run python scripts/sitrep.py --source news --asof $(date -u +%Y%m%d%H%M%S)

# Weather forecasts: Open-Meteo, GloFAS low water, NHC cyclones, NWS alerts
uv run python scripts/sitrep.py --source weather --asof $(date -u +%Y%m%d%H%M%S)

# Authoritative disaster feeds: GDACS, USGS, NOAA tsunami centres
uv run python scripts/sitrep.py --source hazard --asof $(date -u +%Y%m%d%H%M%S)

# Everything at once: news + weather + hazards
uv run python scripts/sitrep.py --source all --asof $(date -u +%Y%m%d%H%M%S)

# Ad-hoc GDELT news query (opt-in: 1 req/5s, bans shared IPs)
uv run python scripts/sitrep.py --live "<query>" --asof $(date -u +%Y%m%d%H%M%S)
```

Adding a publisher means adding a row to `data/feeds.yaml`; weather thresholds
are a row in `data/weather.yaml` - no code change either way. GDELT is
deliberately **not** in the default news path: its retry path costs over a
minute per query and can still return nothing. See
`tasks/free-sources-plan.md` for the measured numbers.

#### Early indicators (forecasts)

Forecasts are the one genuinely *early* signal: a typhoon projected near
Hsinchu in 72h, or GloFAS discharge on the Rhine falling toward low water, is
knowable days before it becomes an observed disruption. A forecast only
becomes a signal when it crosses a threshold in `data/weather.yaml` (90 km/h
winds, 130 km/h gusts, 80 mm/24h rain, 40 °C heat, −15 °C cold), so the
correlator sees signal, not weather noise. Each carries its **lead time**
(`forecast: sustained winds up to 99 km/h at Taiwan Strait on ... (+1d)`) and a
lower credibility prior (0.55 vs 0.9+ for an authority alert). Every SITREP
prints them in an `### Early indicators` table, above the observed-event
scoring, behind an explicit "projections are not observations" caveat and a
coverage note (NHC/NWS are US-only).

A forecast that would have been issued *before* an archived event can be
replayed honestly: Open-Meteo's archived runs hold the forecast actually
issued for a date, and the **previous-runs** API rebuilds each day from the run
that many days ahead of it, so a `(+Nd)` lead time is the model's real horizon
rather than a reanalysis:

```bash
uv run python scripts/backtest_forecast.py suez_2021            # archived run
uv run python scripts/backtest_forecast.py panama_drought_2023 --previous
```

#### Live dashboard feed

The TUI's **Live feeds (news + weather + hazards)** feed polls every 60s over
a 12h look-back, de-duplicates by content id, and never ends, so the board
keeps taking events. `uv run watchtower` then switch the feed dropdown, or
`uv run python scripts/preview_tui.py --feed live` headlessly. The first poll
costs ~20s (it is one request per watched node); later polls hit the hourly
response cache.

## Pipeline

```
SignalSource ─► Correlator ─► SeverityScorer ─► ExposureMatcher
(JSONL replay    geo+time+       5-component      chokepoint graph,
 or live feeds)  entity cluster  rubric, every    criticality ×
                                point explained   distance decay)
                                     │
            AnalogRetriever ◄────────┘  BriefWriters → Alert
            (case-library match         (Health /
             on mechanism+sector+geo)    Wealth / Insurance)
```

- `src/watchtower/agents/` — correlator, severity, exposure, analog,
  briefs, orchestrator (one instance per market vertical)
- `src/watchtower/agents/council.py` — the AI council (filter → research →
  market analyst ⇄ critic → level tier) and `llm.py`, its optional
  provider-agnostic model layer
- `src/watchtower/data/chokepoints.yaml` — ~25-node semiconductor
  supply-chain map (fabs, canals, straits, materials, OSAT, ports)
- `src/watchtower/data/analogs/` — 12 case files; 50 years of supply
  shocks ranked by recurrence × market impact
- `src/watchtower/data/replay/suez_2021.jsonl` — 54 timestamped
  signals (AIS anomalies → wires → SCA advisories → GA declaration),
  including multilingual and noise signals
- `tasks/` — teammate work packages (analog verification, signal
  enrichment)

## Design decisions

- **Rules-first, LLM-optional.** Every score component emits a
  rationale + evidence signal IDs — the audit trail is structural,
  not a prompt artifact. The agent council adds model-written analysis
  when a key is present (`--extra ai`) and degrades to its rule
  fallback otherwise, so the demo needs zero external calls.
- **No-lookahead replays.** `SCENARIO_EXCLUSIONS` drops a scenario's
  own case file from the analog library so the 2021 replay can't
  "cite" the 2021 event — history only knows what happened before.
- **Severity rubric** (weights in `config.py`): event type 0.20,
  chokepoint proximity 0.30, corroboration 0.25, geographic spread
  0.15, escalation rate 0.10 → bands LOW→SEVERE.
- **Extensibility**: markets are YAML config (watchlist, relevant
  node kinds, mechanisms). Semiconductor is fully built; auto and
  energy demonstrate the shape.

## Demo arc

Replay starts Mar 23 2021, ~05:55Z. AIS anomalies and low-credibility
social posts form a GUARDED/ELEVATED cluster; wires and the SCA
suspension push HIGH; queue counts + insurer signals push SEVERE —
before the event dominates mainstream news. The top historical analog
surfaced is the 1956 Suez Crisis (same chokepoint, same mechanism),
followed by Tianjin 2015.
