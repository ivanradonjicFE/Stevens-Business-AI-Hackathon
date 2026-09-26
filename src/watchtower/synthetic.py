"""Synthetic live feed: plausible signals for demos without a live source.

Storylines are anchored to real nodes in the curated chokepoint map and
their mechanism vocabulary matches the historical analog library, so a
synthetic event clusters, escalates, and matches case files exactly like
a real one would. Everything is deterministic under a seed.

Two entry points:

- ``synthetic_signals`` — a time-ordered Signal stream (interleaved
  storylines + ambient noise).
- ``historical_impact_index`` — a 0-1 demo-normalized severity proxy per
  historical analog, so the terminal can chart "then vs now".
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from watchtower.config import load_chokepoints
from watchtower.models import Analog, Signal
from watchtower.sources import _signal_id


@dataclass(frozen=True)
class Storyline:
    """One synthetic supply-chain stress narrative."""

    node_id: str
    kind_hint: str
    headline: str
    # (text, source_type, source_name, lang, credibility) escalation beats
    beats: tuple[tuple[str, str, str, str, float], ...]


# Five concurrent storylines + ambient noise. Beats escalate from rumor
# to official confirmation, which is what drives the severity rubric.
STORYLINES: tuple[Storyline, ...] = (
    Storyline(
        node_id="taiwan_strait",
        kind_hint="extreme_weather",
        headline="Typhoon track shift closes Taiwan Strait to container traffic",
        beats=(
            ("JMA track update: typhoon intensifying, 180 km/h gusts, Taiwan Strait transit advisory", "govt_advisory", "Japan Meteorological Agency", "en", 0.7),
            ("Vessels begin slow-steaming or rerouting east of Taiwan ahead of storm window", "vessel_tracking", "MarineTraffic", "en", 0.6),
            ("Kaohsiung and Keelung ports suspend pilotage for 36 hours as storm approaches", "govt_advisory", "Taiwan MOTC", "en", 0.8),
            ("TSMC activates typhoon protocol; non-essential fab lines idled as precaution", "wire", "Reuters", "en", 0.85),
            ("Taiwan Strait ferry and boxship transits halt; 60+ vessels holding at anchorages", "vessel_tracking", "Kpler", "en", 0.7),
            ("Storm surge forecast for Hsinchu coast; substation hardening under way", "govt_advisory", "Central Weather Administration", "en", 0.75),
            ("Wafer and substrate inbound flows to Taiwan delayed; airfreight spot rates tick up", "analyst", "Sea-Intelligence", "en", 0.7),
            ("Typhoon downgraded but port backlog clearing will run through the weekend", "wire", "AP", "en", 0.85),
            ("Carriers restore Taiwan calls; backlog-clearing estimates 3-5 days", "shipping_line", "Maersk advisory", "en", 0.8),
        ),
    ),
    Storyline(
        node_id="tsmc_hsinchu",
        kind_hint="infrastructure",
        headline="Power disturbance trips advanced-node lines at Hsinchu",
        beats=(
            ("Unconfirmed user reports of flickering power across Hsinchu Science Park", "social", "Twitter @semicast", "en", 0.3),
            ("Taipower: substation fault under investigation, load shed to industrial feeders", "govt_advisory", "Taiwan Power Company", "en", 0.8),
            ("Hsinchu fab cluster reports unplanned downtime; in-process wafers at risk", "trade_press", "DigiTimes", "en", 0.75),
            ("Foundry confirms 'brief interruption'; requalification window being assessed", "wire", "Reuters", "en", 0.9),
            ("Analysts: every 24h of advanced-node downtime tightens lead times further", "analyst", "IHS Markit", "en", 0.75),
            ("Fab operators restore power; yield recovery to take several days", "trade_press", "DigiTimes", "en", 0.7),
            ("Automotive MCU lead times extend on knock-on supply risk", "analyst", "Alphaliner", "en", 0.7),
        ),
    ),
    Storyline(
        node_id="suez_canal",
        kind_hint="shipping_anomaly",
        headline="Mega-container ship loses propulsion inside Suez convoy lane",
        beats=(
            ("EVER-look-alike ULCS reports propulsion failure north of Great Bitter Lake", "vessel_tracking", "VesselFinder", "en", 0.6),
            ("AIS shows convoy slowing; two tugs dispatched from Ismailia", "vessel_tracking", "MarineTraffic", "en", 0.65),
            ("Transit agents: vessel stabilized, towed to anchor, convoys delayed 4-6 hours", "govt_advisory", "GAC Egypt (shipping agent)", "en", 0.85),
            ("Container spot chatter: forwarders quote contingency routings as caution", "trade_press", "Lloyd's List", "en", 0.8),
            ("SCA confirms canal open; queue of ~35 vessels working through backlog", "govt_advisory", "Suez Canal Authority", "en", 0.95),
            ("Insurers note claims risk limited but GA chatter resumed", "insurer", "Lloyd's of London commentary", "en", 0.8),
        ),
    ),
    Storyline(
        node_id="port_shanghai",
        kind_hint="labor_disruption",
        headline="Dockworker action halts berths at Shanghai",
        beats=(
            ("Notice of industrial action circulating among terminal operators", "social", "Weibo logistics", "zh", 0.35),
            ("Terminal operator confirms staggered stoppage; berth productivity down sharply", "trade_press", "Journal of Commerce", "en", 0.75),
            ("Carriers omit Shanghai calls; transshipment pushed to Busan and Singapore", "shipping_line", "Hapag-Lloyd advisory", "en", 0.8),
            ("Backlog builds: 40+ boxships waiting outside Yangshan", "vessel_tracking", "Kpler", "en", 0.7),
            ("Talks scheduled; exporters reroute urgent air freight", "wire", "Bloomberg", "en", 0.85),
            ("Partial work resumption; backlog clearance expected over 1-2 weeks", "shipping_line", "Maersk advisory", "en", 0.8),
        ),
    ),
    Storyline(
        node_id="spruce_pine_quartz",
        kind_hint="materials_shortage",
        headline="High-purity quartz site hit by flooding",
        beats=(
            ("Heavy rainfall triggers flash-flood warning across western North Carolina", "govt_advisory", "NOAA", "en", 0.7),
            ("Local reports of road closures near Spruce Pine mining district", "social", "Twitter @wnc_local", "en", 0.45),
            ("Quartz processor acknowledges operations paused pending safety inspection", "trade_press", "Mining Weekly", "en", 0.75),
            ("Analysts flag crucible feedstock concentration risk for wafer fabs", "analyst", "IHS Markit", "en", 0.75),
            ("Suppliers holding inventory; multi-week outage would test crucible lead times", "trade_press", "DigiTimes", "en", 0.7),
            ("Rail and highway reopen; processor restarts at reduced throughput", "wire", "AP", "en", 0.8),
        ),
    ),
    Storyline(
        node_id="renesas_naka",
        kind_hint="fire",
        headline="Plating line fire halts automotive MCU output at Naka",
        beats=(
            ("Fire reported on a plating line at a Naka fab; employees evacuated", "wire", "NHK", "ja", 0.7),
            ("Fab operator confirms blaze contained; one line damaged, no injuries", "govt_advisory", "Fire and Disaster Management Agency", "ja", 0.85),
            ("Automotive customers notified of potential MCU delivery delays", "trade_press", "Nikkei Asia", "en", 0.8),
            ("Analysts: cleanroom restart and requalification run 3-6 weeks", "analyst", "IHS Markit", "en", 0.75),
            ("Chipmaker says alternate lines will absorb part of the volume", "wire", "Reuters", "en", 0.9),
            ("Automakers begin rationing MCU allocations across model lines", "trade_press", "Automotive News", "en", 0.8),
            ("Insurers estimate business-interruption exposure on the affected line", "insurer", "Lloyd's of London commentary", "en", 0.75),
        ),
    ),
    Storyline(
        node_id="panama_canal",
        kind_hint="drought",
        headline="Canal authority cuts daily transits as reservoir levels drop",
        beats=(
            ("Dry-season rainfall well below normal across the canal watershed", "govt_advisory", "Panama Canal Authority", "en", 0.8),
            ("Authority announces reduced daily booking slots effective next week", "govt_advisory", "Panama Canal Authority", "en", 0.9),
            ("Carriers apply weight limits and surcharges on canal transits", "shipping_line", "Maersk advisory", "en", 0.8),
            ("Queue of waiting boxships lengthens as slots tighten", "vessel_tracking", "Kpler", "en", 0.7),
            ("Analysts: US East Coast Asia imports face 1-2 week schedule risk", "analyst", "Sea-Intelligence", "en", 0.75),
            ("Forwarders quote rail and Suez alternatives at higher cost", "trade_press", "Journal of Commerce", "en", 0.75),
        ),
    ),
    Storyline(
        node_id="micron_hiroshima",
        kind_hint="seismic",
        headline="Offshore quake shakes western Japan DRAM site",
        beats=(
            ("Magnitude 6.4 offshore quake felt across western Honshu", "govt_advisory", "Japan Meteorological Agency", "en", 0.85),
            ("Memory fab initiates seismic protocol; tools held offline for inspection", "trade_press", "DigiTimes", "en", 0.75),
            ("Operator reports no structural damage; wafer starts paused 24h", "wire", "Reuters", "en", 0.9),
            ("Analysts: supply impact limited unless aftershocks delay restart", "analyst", "Alphaliner", "en", 0.7),
            ("DRAM spot chatter picks up on outage speculation", "social", "Twitter @dramwatch", "en", 0.35),
            ("Production resumes at reduced rates; yields under review", "trade_press", "Nikkei Asia", "en", 0.8),
        ),
    ),
    Storyline(
        node_id="strait_of_hormuz",
        kind_hint="geopolitical",
        headline="Tanker seizure raises Hormuz transit risk premium",
        beats=(
            ("Reports of a tanker boarded near the strait approach", "wire", "Reuters", "en", 0.7),
            ("Regional authority confirms vessel diverted for inspection", "govt_advisory", "Port and Maritime Organisation", "en", 0.8),
            ("War-risk premiums for strait transits revised upward", "insurer", "Lloyd's of London commentary", "en", 0.8),
            ("Tanker operators review escorting and routing options", "vessel_tracking", "TankerTrackers", "en", 0.75),
            ("Energy-sensitive suppliers flag fuel cost pass-through risk", "analyst", "IHS Markit", "en", 0.75),
            ("Vessel released; transit normalises but premiums stay elevated", "wire", "Bloomberg", "en", 0.9),
        ),
    ),
    Storyline(
        node_id="ase_kaohsiung",
        kind_hint="labor_disruption",
        headline="Assembly-test site faces strike ballot over shift changes",
        beats=(
            ("Union files notice of strike ballot at the Kaohsiung assembly site", "social", "Twitter @labor_tw", "en", 0.4),
            ("Employer confirms talks; output plan for the week unchanged so far", "wire", "Reuters", "en", 0.85),
            ("Ballot passes; overtime ban begins across assembly and test lines", "trade_press", "DigiTimes", "en", 0.8),
            ("Customers notified of possible packaging and test delays", "trade_press", "Journal of Commerce", "en", 0.75),
            ("Mediation scheduled; overtime ban holds in the meantime", "wire", "AP", "en", 0.85),
        ),
    ),
    Storyline(
        node_id="penang_osat",
        kind_hint="flood",
        headline="Monsoon flooding interrupts Penang assembly cluster",
        beats=(
            ("Monsoon downpours trigger flood warnings across Penang state", "govt_advisory", "Malaysian Meteorological Department", "en", 0.8),
            ("Industrial park access roads submerged; shifts cancelled", "social", "Twitter @penang_news", "en", 0.5),
            ("Assembly house confirms operations paused pending drainage checks", "trade_press", "The Star", "en", 0.75),
            ("Analysts flag broad assembly-test exposure across the cluster", "analyst", "IHS Markit", "en", 0.75),
            ("Water recedes; two of three lines restart on backup power", "wire", "Reuters", "en", 0.9),
        ),
    ),
)

# Ambient noise: real-looking but irrelevant, to prove filtering works.
NOISE = (
    ("LA/LB anchorage count steady at 24 boxships; congestion unchanged", "vessel_tracking", "MarineTraffic", "port_la_lb", 0.6),
    ("Routine crane maintenance scheduled at Long Beach this weekend", "noise", "port wire", "port_la_lb", 0.5),
    ("US Treasury yields steady; no fresh macro catalyst", "market", "market wire", "port_singapore", 0.5),
    ("Tropical disturbance forms in western Pacific; no lanes threatened", "noise", "weather wire", "port_busan", 0.5),
    ("Bunker prices flat week-over-week at Singapore", "market", "bunker desk", "port_singapore", 0.6),
    ("Malacca transit volumes normal; no anomalies in AIS density", "vessel_tracking", "Kpler", "strait_of_malacca", 0.65),
    ("DRAM contract price talk quiet ahead of quarterly negotiations", "market", "market wire", "samsung_pyeongtaek", 0.55),
    ("Hormuz tanker transits nominal; insurance rates unchanged", "vessel_tracking", "TankerTrackers", "strait_of_hormuz", 0.65),
    ("Rotterdam container dwell time steady at 3.1 days", "vessel_tracking", "port wire", "suez_canal", 0.6),
    ("Air-freight rates from Taiwan flat; capacity ample", "market", "air cargo desk", "taiwan_strait", 0.6),
    ("Taiwan earthquake insurance renewals proceed normally", "insurer", "insurance press", "tsmc_tainan", 0.6),
    ("Neon spot offers quiet; no supply concerns flagged", "market", "gas desk", "neon_odesa", 0.55),
    ("Substrate lead times unchanged quarter-over-quarter", "analyst", "IHS Markit", "ajinomoto_abf", 0.65),
    ("Odesa corridor grain transits continue under existing framework", "wire", "Reuters", "neon_odesa", 0.75),
    ("Suez convoys on schedule; no delays reported", "govt_advisory", "GAC Egypt (shipping agent)", "suez_canal", 0.85),
    ("Automotive inventories at dealers stable month-over-month", "market", "market wire", "samsung_austin", 0.55),
    ("Hsinchu Science Park power quality nominal this week", "govt_advisory", "Taiwan Power Company", "tsmc_hsinchu", 0.8),
    ("Long Beach truck turn times improve; no backlog", "vessel_tracking", "port wire", "port_la_lb", 0.6),
)


def _entity_tokens(name: str, node_id: str) -> tuple[str, ...]:
    """Entity tokens that let the correlator join a storyline's signals."""
    toks = [w.lower() for w in name.replace("/", " ").split() if len(w) > 3]
    toks.append(node_id.replace("_", " "))
    return tuple(dict.fromkeys(toks))


def synthetic_signals(
    *,
    seed: int = 7,
    start_ts: float | None = None,
    horizon_h: float = 72.0,
    noise_ratio: float = 1.0,
) -> list[Signal]:
    """Build a time-ordered synthetic Signal stream.

    Each storyline emits its beats across a random slice of the horizon
    (all within the correlator's 48h join window), interleaved with
    ambient noise. Deterministic for a given seed.
    """
    rng = random.Random(seed)
    nodes = {n.node_id: n for n in load_chokepoints()}
    t0 = start_ts or datetime(2026, 8, 3, 6, 0, tzinfo=UTC).timestamp()
    signals: list[Signal] = []

    for story in STORYLINES:
        node = nodes.get(story.node_id)
        if node is None:
            continue
        entities = _entity_tokens(node.name, node.node_id)
        # Storyline start: 0-12h in; beats spaced within a 30h window.
        t = t0 + rng.uniform(0, 12) * 3600
        # keep every storyline inside the correlator's 48h join window
        step = max(1.0, min(30.0, horizon_h) * 3600 / max(1, len(story.beats) - 1))
        for i, (text, src_type, src_name, lang, cred) in enumerate(story.beats):
            ts = t + i * step * rng.uniform(0.8, 1.2)
            lat = node.geo[0] + rng.uniform(-0.6, 0.6)
            lon = node.geo[1] + rng.uniform(-0.6, 0.6)
            signals.append(
                Signal(
                    ts=ts,
                    source_type=src_type,
                    source_name=src_name,
                    url=f"https://example.org/synthetic/{story.node_id}/{i}",
                    text=text,
                    geo=(lat, lon),
                    entities=entities,
                    lang=lang,
                    kind_hint=story.kind_hint,
                    credibility=cred,
                    signal_id=_signal_id(ts, src_name, text),
                )
            )

    n_noise = max(0, round(len(signals) * noise_ratio / 3))
    for i in range(n_noise):
        text, src_type, src_name, node_id, cred = NOISE[i % len(NOISE)]
        node = nodes.get(node_id)
        if node is None:
            continue
        ts = t0 + rng.uniform(0, horizon_h) * 3600
        signals.append(
            Signal(
                ts=ts,
                source_type=src_type,
                source_name=src_name,
                url=f"https://example.org/synthetic/noise/{i}",
                text=text,
                geo=(node.geo[0] + rng.uniform(-0.4, 0.4), node.geo[1] + rng.uniform(-0.4, 0.4)),
                entities=_entity_tokens(node.name, node.node_id),
                lang="en",
                kind_hint="noise" if src_type == "noise" else "market",
                credibility=cred,
                signal_id=_signal_id(ts, src_name, text),
            )
        )

    return sorted(signals, key=lambda s: s.ts)


def historical_impact_index(analog: Analog) -> float:
    """0-1 demo-normalized impact proxy for a historical case file.

    Weighted blend of duration, mechanism breadth, quantified impact
    richness, and market-reaction breadth — transparent and stable so
    the terminal can chart "then vs now" without inventing numbers
    from thin air.
    """
    duration = min(1.0, analog.duration_days / 45.0)
    mechanisms = min(1.0, len(analog.mechanisms) / 5.0)
    quantified = min(1.0, len(analog.quantified_impact) / 3.0)
    market = min(1.0, len(analog.market_reaction) / 3.0)
    return round(
        0.35 * duration + 0.25 * mechanisms + 0.20 * quantified + 0.20 * market,
        3,
    )


def historical_comparison(
    analogs: list[Analog],
) -> list[tuple[Analog, float]]:
    """Rank historical case files by impact index, descending."""
    scored = [(a, historical_impact_index(a)) for a in analogs]
    return sorted(scored, key=lambda p: p[1], reverse=True)


def comparison_rows(analogs: list[Analog], top_n: int = 6) -> list[tuple[str, float]]:
    """(short_name, index) rows ready for a bar chart."""
    rows = []
    for analog, index in historical_comparison(analogs)[:top_n]:
        short = analog.name if len(analog.name) <= 26 else analog.name[:25] + "…"
        rows.append((short, index))
    return rows
