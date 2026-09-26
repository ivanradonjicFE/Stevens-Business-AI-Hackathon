# Supply-Chain Situation Report

**Report type:** Point-in-time (PIT) — 2026-09-25 09:00 UTC
**Information cutoff:** 2026-09-25 09:00 UTC. This report is strictly point-in-time: it reflects only signals received on or before the cutoff. No post-cutoff data, hindsight, or future market outcomes inform any score, projection, or precedent shown.

---

## [ELEVATED] Storm — composite 0.36

**Lead source:** NASA EONET (govt_advisory)
https://eonet.gsfc.nasa.gov/api/v3/events/EONET_24787

**Corroboration:** 2 signals, 1 source types | **Region:** middle_east | **Market lens:** semicon
**Relevance to semicon:** 0.08 / 1 (cosine similarity, signal text vs market context)

### Weather Disruption Index (WDI)

**21 / 100 — GREEN**

| Block | Factor | Raw (0-10) | Weight |
|---|---|---|---|
| Severity | intensity | 5.5 | 0.40 |
| Severity | radius | 6.7 | 0.20 |
| Severity | duration | 1.7 | 0.40 |
| **Severity total** | | **4.2** | |
| Vulnerability | concentration | 4.8 | 0.40 |
| Vulnerability | inventory buffers | 4.0 | 0.20 |
| Vulnerability | utility dependency | 6.0 | 0.40 |
| **Vulnerability total** | | **5.1** | |
| **WDI = severity × vulnerability** | **4.2 × 5.1** | **21** | — |

### Historical precedents

| Event | Date | Match | Documented insured impact | Weight |
|---|---|---|---|---|
| Suez Crisis canal closure | 1956-10-29 | 0.40 | — | w=0.40 |
| Texas Winter Storm Uri fab shutdowns | 2021-02-14 | 0.26 | $360M | w=0.26 |
| Ever Given Suez Canal blockage | 2021-03-23 | 0.25 | $550M | w=0.25 |

### Projected insured impact

Match-weighted mean of documented analog losses: **$453M** (precedent range $360M–$550M; each weight is the row's match score above). **EXCEEDS** the PCS $25M catastrophe-designation threshold. **Thin precedent set — treat as order-of-magnitude only, not a booking estimate.**

### Recommended insurer actions

- **Monitor:** hold new-binding review in the affected region; no claims surge expected at current severity, but the corroboration trend justifies a daily re-score.
- **Reinsurance:** projected exposure exceeds the PCS $25M threshold but rests on a thin precedent set — verify analog basis before any notification.
- **Portfolio triage:** proximity-ranked watchlist — Bab el-Mandeb Strait, Strait of Hormuz. Pull exposure reports and flag concentration above retention thresholds for proactive outreach/mitigation.

### How we scored this

- 2 signals from 1 distinct sources; hazard composite 0.36 via rubric (type/proximity/corroboration/spread/escalation).
- Exposure = distance-decayed chokepoint criticality; vulnerability = sector-resilience prior per node kind.
- Analogs picked by mechanism/sector/geo similarity; projected loss = match-weighted mean of documented analog losses — no severity scaling; every dollar traces to a named case file.
- Caveat: automated early-warning signal, not underwriting advice. Replay data is curated and point-in-time; projections are analog-derived estimates, not observed outcomes.
