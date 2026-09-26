# Supply-Chain Situation Report

**Report type:** Point-in-time (PIT) — 2026-09-22 03:58 UTC
**Information cutoff:** 2026-09-22 03:58 UTC. This report is strictly point-in-time: it reflects only signals received on or before the cutoff. No post-cutoff data, hindsight, or future market outcomes inform any score, projection, or precedent shown.

---

## [ELEVATED] Taiwan — composite 0.48

**Lead source:** finance.biggo.com (trade_press)
https://news.google.com/rss/articles/CBMidkFVX3lxTFBoSTdTSG5HaGh5c1hEcHJRdXNyckVvSDdJTXFCXzMzb0Q4QVN0MTNuclFvT0Vjb0NCTXlHQXRITWxyRGthVzFiTzdZckNnZUktcmpiZUsxRUh3bkQ5T01rLTZHV3BCNlBZeDc5R0FQd3ZvQWR1T3c?oc=5

**Corroboration:** 3 signals, 1 source types | **Region:** east_asia | **Market lens:** semicon
**Relevance to semicon:** 0.14 / 1 (cosine similarity, signal text vs market context)

### Weather Disruption Index (WDI)

**25 / 100 — GREEN**

| Block | Factor | Raw (0-10) | Weight |
|---|---|---|---|
| Severity | intensity | 5.0 | 0.40 |
| Severity | radius | 3.3 | 0.20 |
| Severity | duration | 1.7 | 0.40 |
| **Severity total** | | **3.3** | |
| Vulnerability | concentration | 10.0 | 0.40 |
| Vulnerability | inventory buffers | 2.9 | 0.20 |
| Vulnerability | utility dependency | 7.1 | 0.40 |
| **Vulnerability total** | | **7.4** | |
| **WDI = severity × vulnerability** | **3.3 × 7.4** | **25** | — |

### Historical precedents

| Event | Date | Match | Documented insured impact | Weight |
|---|---|---|---|---|
| Great Hanshin (Kobe) earthquake | 1995-01-17 | 0.36 | — | w=0.36 |
| Baltimore Key Bridge collapse — port channel closure | 2024-03-26 | 0.35 | — | w=0.35 |
| Ever Given Suez Canal blockage | 2021-03-23 | 0.35 | $550M | w=0.35 |
| Red Sea attacks — Bab el-Mandeb rerouting | 2023-12-15 | 0.33 | — | w=0.33 |

### Projected insured impact

Match-weighted mean of documented analog losses: **$550M** (precedent range $550M–$550M; each weight is the row's match score above). **EXCEEDS** the PCS $25M catastrophe-designation threshold. **Thin precedent set — treat as order-of-magnitude only, not a booking estimate.**

### Recommended insurer actions

- **Monitor:** hold new-binding review in the affected region; no claims surge expected at current severity, but the corroboration trend justifies a daily re-score.
- **Reinsurance:** projected exposure exceeds the PCS $25M threshold but rests on a thin precedent set — verify analog basis before any notification.
- **Portfolio triage:** proximity-ranked watchlist — TSMC Hsinchu HQ/fab cluster, Taiwan Strait, TSMC Tainan (Fab 18). Pull exposure reports and flag concentration above retention thresholds for proactive outreach/mitigation.

### How we scored this

- 3 signals from 3 distinct sources; hazard composite 0.48 via rubric (type/proximity/corroboration/spread/escalation).
- Exposure = distance-decayed chokepoint criticality; vulnerability = sector-resilience prior per node kind.
- Analogs picked by mechanism/sector/geo similarity; projected loss = match-weighted mean of documented analog losses — no severity scaling; every dollar traces to a named case file.
- Caveat: automated early-warning signal, not underwriting advice. Live feeds are point-in-time as of the cutoff; projections are analog-derived estimates, not observed outcomes.

## AI council read

| field | value |
|---|---|
| Council level | WATCH - notifiable |
| Trigger | new event |
| Origin | rule fallback (no model reachable) |
| Admitted via | exposure route |
| Lexical relevance | 0.14 of 1 (deterministic, computed from the market config) |
| Context tokens hit | on, packaging |

**Assessment.** Closest precedent Great Hanshin (Kobe) earthquake (1995-01-17, match 0.36): Limited broad-market reaction (pre-globalisation of supply chains); regional industrial output fell sharply

**Research brief.** Taiwan — ELEVATED band, 3 signals across 1 source types. Nearest exposure TSMC Hsinchu HQ/fab cluster (0.89); closest precedent Great Hanshin (Kobe) earthquake (1995-01-17).

**Market view.** Closest precedent Great Hanshin (Kobe) earthquake (1995-01-17, match 0.36): Limited broad-market reaction (pre-globalisation of supply chains); regional industrial output fell sharply Great Hanshin (Kobe) earthquake ran 90 days; recorded lead-time effect: +weeks for components routed through Hanshin plants. direction negative, confidence low.

**Critique.** Rule-based review (no model critique available); rule tier kept. Admitted via the exposure route at lexical relevance 0.14.

| segment | tickers | rationale |
|---|---|---|
| foundry | TSM, UMC, GFS | touched via autos, container_shipping, electronics, energy, retail |
| logic | NVDA, AMD, AVGO | touched via autos, container_shipping, electronics, energy, retail |
| analog | ADI, TXN | touched via autos, container_shipping, electronics, energy, retail |
