# Watchtower — semiconductor supply-shock early warning

Chubb Challenge 6 (Stevens Business + AI Hackathon). A multi-agent
research pipeline that correlates weak public signals into candidate
events, scores severity with an auditable rubric, measures exposure
against a semiconductor chokepoint map, retrieves historical analogs,
and emits Health / Wealth / Insurance impact briefs in a terminal UI.

## Run it

```bash
uv sync
uv run watchtower                      # TUI demo, replays Suez 2021
uv run python scripts/replay.py        # headless replay (smoke test)
uv run pytest                          # tests
```

TUI keys: `space` pause/resume · `enter` event audit view ·
`r` restart · `q` quit · market dropdown switches verticals.

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
  not a prompt artifact. An LLM backend can be slotted in for
  narrative polish, but the demo needs zero external calls.
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
