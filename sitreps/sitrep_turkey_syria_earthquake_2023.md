# Supply-Chain Situation Report

**Report type:** Point-in-time (PIT) — 2023-02-12 13:30 UTC
**Information cutoff:** 2023-02-12 13:30 UTC. This report is strictly point-in-time: it reflects only signals received on or before the cutoff. No post-cutoff data, hindsight, or future market outcomes inform any score, projection, or precedent shown.

---

## [SEVERE] Turkey — composite 0.80

**Lead source:** USGS Earthquake Hazards Program (govt_advisory)
https://earthquake.usgs.gov

**Corroboration:** 56 signals, 8 source types | **Region:** middle_east | **Market lens:** semicon
**Relevance to semicon:** 0.03 / 1 (cosine similarity, signal text vs market context)

### Weather Disruption Index (WDI)

**66 / 100 — RED**

| Block | Factor | Raw (0-10) | Weight |
|---|---|---|---|
| Severity | intensity | 7.5 | 0.40 |
| Severity | radius | 10.0 | 0.20 |
| Severity | duration | 10.0 | 0.40 |
| **Severity total** | | **9.0** | |
| Vulnerability | concentration | 10.0 | 0.40 |
| Vulnerability | inventory buffers | 3.3 | 0.20 |
| Vulnerability | utility dependency | 6.7 | 0.40 |
| **Vulnerability total** | | **7.3** | |
| **WDI = severity × vulnerability** | **9.0 × 7.3** | **66** | — |

### Historical precedents

| Event | Date | Match | Documented insured impact | Weight |
|---|---|---|---|---|
| Ever Given Suez Canal blockage | 2021-03-23 | 0.42 | $550M | w=0.42 |
| Suez Crisis canal closure | 1956-10-29 | 0.33 | — | w=0.33 |
| Tianjin port explosion | 2015-08-12 | 0.31 | $3,500M | w=0.31 |

### Projected insured impact

Match-weighted mean of documented analog losses: **$1,786M** (precedent range $550M–$3,500M; each weight is the row's match score above). **EXCEEDS** the PCS $25M catastrophe-designation threshold.

### Recommended insurer actions

- **Declare CAT readiness now:** initiate PCS-style catastrophe tracking for the affected region — spin up a dedicated claims serial number and reserve committee review before policyholder volume spikes.
- **Claims capacity:** pre-stage adjusters and surge call-center staffing for the footprint; expect first notices of loss within 24–72h of the exposure event.
- **Underwriting hold:** pause new-binding authority in the affected geography/sector pending loss confirmation.
- **Reinsurance notification:** projected $1,786M clears the PCS $25M designation line — notify reinsurance partners and verify attachment-point availability for the exposed portfolio.
- **Portfolio triage:** proximity-ranked watchlist — Port of Iskenderun, Ceyhan oil terminal, Suez Canal. Pull exposure reports and flag concentration above retention thresholds for proactive outreach/mitigation.

### How we scored this

- 56 signals from 41 distinct sources; hazard composite 0.80 via rubric (type/proximity/corroboration/spread/escalation).
- Exposure = distance-decayed chokepoint criticality; vulnerability = sector-resilience prior per node kind.
- Analogs picked by mechanism/sector/geo similarity; projected loss = match-weighted mean of documented analog losses, scaled by severity ratio (capped 0.33–1.5×).
- Caveat: automated early-warning signal, not underwriting advice. Replay data is curated and point-in-time; projections are analog-derived estimates, not observed outcomes.
