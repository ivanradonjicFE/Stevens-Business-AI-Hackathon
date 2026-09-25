# Supply-Chain Situation Report

**Report type:** Point-in-time (PIT) — 2021-03-30 08:00 UTC
**Information cutoff:** 2021-03-30 08:00 UTC. This report is strictly point-in-time: it reflects only signals received on or before the cutoff. No post-cutoff data, hindsight, or future market outcomes inform any score, projection, or precedent shown.

---

## [SEVERE] Suez Canal — composite 0.76

**Lead source:** Suez Canal Authority (govt_advisory)
https://www.suezcanal.gov.eg

**Corroboration:** 49 signals, 9 source types | **Region:** middle_east | **Market lens:** semicon
**Relevance to semicon:** 0.3 / 1 (cosine similarity, signal text vs market context)

### Weather Disruption Index (WDI)

**44 / 100 — ORANGE**

| Block | Factor | Raw (0-10) | Weight |
|---|---|---|---|
| Severity | intensity | 8.5 | 0.40 |
| Severity | radius | 10.0 | 0.20 |
| Severity | duration | 1.7 | 0.40 |
| **Severity total** | | **6.1** | |
| Vulnerability | concentration | 10.0 | 0.40 |
| Vulnerability | inventory buffers | 3.7 | 0.20 |
| Vulnerability | utility dependency | 6.3 | 0.40 |
| **Vulnerability total** | | **7.3** | |
| **WDI = severity × vulnerability** | **6.1 × 7.3** | **44** | — |

### Historical precedents

| Event | Date | Match | Documented insured impact | Weight |
|---|---|---|---|---|
| Suez Crisis canal closure | 1956-10-29 | 0.46 | — | w=0.46 |
| Tianjin port explosion | 2015-08-12 | 0.30 | $3,500M | w=0.30 |
| Texas Winter Storm Uri fab shutdowns | 2021-02-14 | 0.17 | $360M | w=0.17 |

### Projected insured impact

Match-weighted mean of documented analog losses: **$2,338M** (precedent range $360M–$3,500M; each weight is the row's match score above). **EXCEEDS** the PCS $25M catastrophe-designation threshold.

### Recommended insurer actions

- **Declare CAT readiness now:** initiate PCS-style catastrophe tracking for the affected region — spin up a dedicated claims serial number and reserve committee review before policyholder volume spikes.
- **Claims capacity:** pre-stage adjusters and surge call-center staffing for the footprint; expect first notices of loss within 24–72h of the exposure event.
- **Underwriting hold:** pause new-binding authority in the affected geography/sector pending loss confirmation.
- **Reinsurance notification:** projected $2,338M clears the PCS $25M designation line — notify reinsurance partners and verify attachment-point availability for the exposed portfolio.
- **Portfolio triage:** proximity-ranked watchlist — Suez Canal, Port of Iskenderun, Ceyhan oil terminal. Pull exposure reports and flag concentration above retention thresholds for proactive outreach/mitigation.

### How we scored this

- 49 signals from 36 distinct sources; hazard composite 0.76 via rubric (type/proximity/corroboration/spread/escalation).
- Exposure = distance-decayed chokepoint criticality; vulnerability = sector-resilience prior per node kind.
- Analogs picked by mechanism/sector/geo similarity; projected loss = match-weighted mean of documented analog losses, scaled by severity ratio (capped 0.33–1.5×).
- Caveat: automated early-warning signal, not underwriting advice. Replay data is curated and point-in-time; projections are analog-derived estimates, not observed outcomes.
