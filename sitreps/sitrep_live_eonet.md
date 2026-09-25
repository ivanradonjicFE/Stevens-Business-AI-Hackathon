# Supply-Chain Situation Report

**Report type:** Point-in-time (PIT) — 2021-08-31 15:00 UTC
**Information cutoff:** 2021-08-31 15:00 UTC. This report is strictly point-in-time: it reflects only signals received on or before the cutoff. No post-cutoff data, hindsight, or future market outcomes inform any score, projection, or precedent shown.

---

## [GUARDED] Storm — composite 0.33

**Lead source:** NASA EONET (govt_advisory)
https://eonet.gsfc.nasa.gov/api/v3/events/EONET_5900

**Corroboration:** 4 signals, 1 source types | **Region:** north_america | **Market lens:** energy
**Relevance to energy:** 0.0 / 1 (cosine similarity, signal text vs market context)

### Weather Disruption Index (WDI)

**14 / 100 — GREEN**

| Block | Factor | Raw (0-10) | Weight |
|---|---|---|---|
| Severity | intensity | 5.5 | 0.40 |
| Severity | radius | 10.0 | 0.20 |
| Severity | duration | 1.7 | 0.40 |
| **Severity total** | | **4.9** | |
| Vulnerability | concentration | 0.0 | 0.40 |
| Vulnerability | inventory buffers | 6.0 | 0.20 |
| Vulnerability | utility dependency | 4.0 | 0.40 |
| **Vulnerability total** | | **2.8** | |
| **WDI = severity × vulnerability** | **4.9 × 2.8** | **14** | — |

### Historical precedents

| Event | Date | Match | Documented insured impact | Weight |
|---|---|---|---|---|
| Texas Winter Storm Uri fab shutdowns | 2021-02-14 | 0.29 | $360M | w=0.29 |
| Taiwan drought and fab water restrictions | 2021-03-01 | 0.18 | — | w=0.18 |
| Thailand floods | 2011-07-25 | 0.14 | $20,000M | w=0.14 |

### Projected insured impact

Match-weighted mean of documented analog losses: **$6,749M** (precedent range $360M–$20,000M; each weight is the row's match score above). **EXCEEDS** the PCS $25M catastrophe-designation threshold. **Thin precedent set — treat as order-of-magnitude only, not a booking estimate.**

### Recommended insurer actions

- **Monitor:** hold new-binding review in the affected region; no claims surge expected at current severity, but the corroboration trend justifies a daily re-score.
- **Reinsurance:** projected exposure exceeds the PCS $25M threshold but rests on a thin precedent set — verify analog basis before any notification.
- **Portfolio triage:** proximity-ranked watchlist — the affected region. Pull exposure reports and flag concentration above retention thresholds for proactive outreach/mitigation.

### How we scored this

- 4 signals from 1 distinct sources; hazard composite 0.33 via rubric (type/proximity/corroboration/spread/escalation).
- Exposure = distance-decayed chokepoint criticality; vulnerability = sector-resilience prior per node kind.
- Analogs picked by mechanism/sector/geo similarity; projected loss = match-weighted mean of documented analog losses, scaled by severity ratio (capped 0.33–1.5×).
- Caveat: automated early-warning signal, not underwriting advice. Replay data is curated and point-in-time; projections are analog-derived estimates, not observed outcomes.
