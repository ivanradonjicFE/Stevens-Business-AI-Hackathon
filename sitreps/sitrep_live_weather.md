# Supply-Chain Situation Report

**Report type:** Point-in-time (PIT) — 2026-09-26 04:42 UTC
**Information cutoff:** 2026-09-26 04:42 UTC. This report is strictly point-in-time: it reflects only signals received on or before the cutoff. No post-cutoff data, hindsight, or future market outcomes inform any score, projection, or precedent shown.

---

## [HIGH] Spruce — composite 0.57

**Lead source:** NWS (govt_advisory)
urn:oid:2.49.0.1.840.0.94b26898c77d6e3cfc261701b373f71d9e50885f.001.1

**Corroboration:** 10 signals, 1 source types | **Region:** north_america | **Market lens:** semicon
**Relevance to semicon:** 0.07 / 1 (cosine similarity, signal text vs market context)

### Early indicators (forecasts)

| what the models say | lead time | source | credibility |
|---|---|---|---|
| forecast: river discharge falling to 52.1 m3/s at Port of Shanghai on 2026-09-26 (+0d), the 10th percentile of the last 92 days (GloFAS) | +0d | GloFAS | 0.55 |

*Projections, not observations: each row states the model and its lead time, and carries a lower credibility prior (0.55) than an authority alert. These are the forecasts nearest this event across the live feed, not confirmed disruptions. US warnings (NWS) cover the US only; GDACS/EONET/Open-Meteo are global.*

### Weather Disruption Index (WDI)

**52 / 100 — ORANGE**

| Block | Factor | Raw (0-10) | Weight |
|---|---|---|---|
| Severity | intensity | 5.5 | 0.40 |
| Severity | radius | 10.0 | 0.20 |
| Severity | duration | 10.0 | 0.40 |
| **Severity total** | | **8.2** | |
| Vulnerability | concentration | 6.7 | 0.40 |
| Vulnerability | inventory buffers | 1.9 | 0.20 |
| Vulnerability | utility dependency | 8.1 | 0.40 |
| **Vulnerability total** | | **6.3** | |
| **WDI = severity × vulnerability** | **8.2 × 6.3** | **52** | — |

### Historical precedents

| Event | Date | Match | Documented insured impact | Weight |
|---|---|---|---|---|
| Hurricane Helene — Spruce Pine quartz flooding | 2024-09-26 | 0.49 | — | w=0.49 |
| Chennai floods (South India) | 2015-11-15 | 0.47 | — | w=0.47 |
| Texas Winter Storm Uri fab shutdowns | 2021-02-14 | 0.46 | $360M | w=0.46 |
| Thailand floods | 2011-07-25 | 0.43 | $20,000M | w=0.43 |

### Projected insured impact

Match-weighted mean of documented analog losses: **$9,778M** (precedent range $360M–$20,000M; each weight is the row's match score above). **EXCEEDS** the PCS $25M catastrophe-designation threshold.

### Recommended insurer actions

- **Declare CAT readiness now:** initiate PCS-style catastrophe tracking for the affected region — spin up a dedicated claims serial number and reserve committee review before policyholder volume spikes.
- **Claims capacity:** pre-stage adjusters and surge call-center staffing for the footprint; expect first notices of loss within 24–72h of the exposure event.
- **Underwriting hold:** pause new-binding authority in the affected geography/sector pending loss confirmation.
- **Reinsurance notification:** projected $9,778M clears the PCS $25M designation line — notify reinsurance partners and verify attachment-point availability for the exposed portfolio.
- **Portfolio triage:** proximity-ranked watchlist — Spruce Pine quartz (NC), Samsung Austin S2. Pull exposure reports and flag concentration above retention thresholds for proactive outreach/mitigation.

### How we scored this

- 10 signals from 1 distinct sources; hazard composite 0.57 via rubric (type/proximity/corroboration/spread/escalation).
- Exposure = distance-decayed chokepoint criticality; vulnerability = sector-resilience prior per node kind.
- Analogs picked by mechanism/sector/geo similarity; projected loss = match-weighted mean of documented analog losses — no severity scaling; every dollar traces to a named case file.
- Caveat: automated early-warning signal, not underwriting advice. Live feeds are point-in-time as of the cutoff; projections are analog-derived estimates, not observed outcomes.

## AI council read

No council brief for this event yet (the council analyses new or escalating events only).
