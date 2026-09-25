# Watchtower — Semiconductor Supply-Shock Early Warning

Chubb Challenge 6: multi-agent deep-research system that detects emerging
supply-chain disruptions early, correlates weak signals, scores severity with
an auditable rationale, and produces audience-specific impact briefs.

## Design

Replay-driven demo: the pipeline replays the Ever Given / Suez grounding
(Mar 2021) from timestamped signals, flagging the event as it unfolds —
deterministic and auditable. Same pipeline supports live feeds (stubbed).

```
SignalSource ─► Correlator ─► SeverityScorer ─► ExposureMatcher
(replay/live)    (geo+time+      (rubric:        (chokepoint
                  entity          type, prox,      graph,
                  clustering)     corroboration,   geodesic)
                                  spread)              │
                                AnalogRetriever ◄──────┘
                                (10-case library,
                                 mechanism+geo match)
                                      │
        BriefWriters (Health / Wealth / Insurance) ─► TUI
```

- Rules-first: every score emits `rationale` + evidence signal IDs. No LLM
  required; enrichment is a pluggable slot if a key appears.
- Markets are config-driven verticals (semiconductor fully built; auto/energy
  stubbed) — demonstrates extensibility.

## Analog library selection method

50 years of supply shocks ranked by (recurrence frequency × documented market
impact), top 10 kept. Each case file documents timeline, market reaction,
lead-time deltas, insurance losses, and mechanism tags.

## Layout

- `src/watchtower/` — package (models, agents, data, ui)
- `src/watchtower/data/` — chokepoints.yaml, markets.yaml, analogs/*.yaml
- `src/watchtower/data/replay/` — replayable signal streams (suez_2021.jsonl)
- `tasks/` — teammate work packages
- `scripts/replay.py` — headless replay (smoke test)

## Constraints

5-hour build, local/free only, 16GB Intel Mac. Deterministic core, no paid
APIs, no heavyweight agent frameworks.
