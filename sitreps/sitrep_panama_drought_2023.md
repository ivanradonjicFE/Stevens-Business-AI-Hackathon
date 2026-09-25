# Supply-Chain Situation Report

**Report type:** Point-in-time (PIT) — 2023-08-10 13:45 UTC
**Information cutoff:** 2023-08-10 13:45 UTC. This report is strictly point-in-time: it reflects only signals received on or before the cutoff. No post-cutoff data, hindsight, or future market outcomes inform any score, projection, or precedent shown.

---

## [HIGH] Panama Canal — composite 0.67

**Lead source:** Panama Canal Authority (ACP) (port_authority)
https://pancanal.com

**Corroboration:** 43 signals, 8 source types | **Region:** latin_america | **Market lens:** semicon
**Relevance to semicon:** 0.33 / 1 (cosine similarity, signal text vs market context)

### Weather Disruption Index (WDI)

**37 / 100 — ORANGE**

| Block | Factor | Raw (0-10) | Weight |
|---|---|---|---|
| Severity | intensity | 5.0 | 0.40 |
| Severity | radius | 6.7 | 0.20 |
| Severity | duration | 8.3 | 0.40 |
| **Severity total** | | **6.7** | |
| Vulnerability | concentration | 5.9 | 0.40 |
| Vulnerability | inventory buffers | 4.0 | 0.20 |
| Vulnerability | utility dependency | 6.0 | 0.40 |
| **Vulnerability total** | | **5.6** | |
| **WDI = severity × vulnerability** | **6.7 × 5.6** | **37** | — |

### Historical precedents

| Event | Date | Match | Documented insured impact | Weight |
|---|---|---|---|---|
| Suez Crisis canal closure | 1956-10-29 | 0.30 | — | w=0.30 |
| Texas Winter Storm Uri fab shutdowns | 2021-02-14 | 0.25 | $360M | w=0.25 |
| Ever Given Suez Canal blockage | 2021-03-23 | 0.22 | $550M | w=0.22 |

### Projected insured impact

Match-weighted mean of documented analog losses: **$449M** (precedent range $360M–$550M; each weight is the row's match score above). **EXCEEDS** the PCS $25M catastrophe-designation threshold.

### Recommended insurer actions

- **Declare CAT readiness now:** initiate PCS-style catastrophe tracking for the affected region — spin up a dedicated claims serial number and reserve committee review before policyholder volume spikes.
- **Claims capacity:** pre-stage adjusters and surge call-center staffing for the footprint; expect first notices of loss within 24–72h of the exposure event.
- **Underwriting hold:** pause new-binding authority in the affected geography/sector pending loss confirmation.
- **Reinsurance notification:** projected $449M clears the PCS $25M designation line — notify reinsurance partners and verify attachment-point availability for the exposed portfolio.
- **Portfolio triage:** proximity-ranked watchlist — Panama Canal. Pull exposure reports and flag concentration above retention thresholds for proactive outreach/mitigation.

### How we scored this

- 43 signals from 30 distinct sources; hazard composite 0.67 via rubric (type/proximity/corroboration/spread/escalation).
- Exposure = distance-decayed chokepoint criticality; vulnerability = sector-resilience prior per node kind.
- Analogs picked by mechanism/sector/geo similarity; projected loss = match-weighted mean of documented analog losses, scaled by severity ratio (capped 0.33–1.5×).
- Caveat: automated early-warning signal, not underwriting advice. Replay data is curated and point-in-time; projections are analog-derived estimates, not observed outcomes.
