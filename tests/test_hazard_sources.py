"""Tests for the hazard scouts: GDACS, USGS, NOAA tsunami centres.

Every test drives the parsers with a canned payload shaped like the real
feed response, so the suite stays offline and deterministic while still
covering the things that break in production: point-in-time cutoffs,
tsunami-flag filtering, severity vocabulary and cross-source de-duping.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from watchtower import config, sources
from watchtower.models import Signal

# The NOAA bulletin and the USGS flash below describe the same Chilean
# quake: the warning centre shouts about thirty minutes after it happens,
# which is exactly what lets the de-duplicator see they are one event.
BULLETIN_TS = datetime(2026, 9, 20, 7, 30, tzinfo=UTC).timestamp()
QUAKE_MS = int((BULLETIN_TS - 1800) * 1000)

GDACS_TC = {
    "features": [
        {
            "geometry": {"coordinates": [121.0, 24.0]},
            "properties": {
                "eventtype": "TC",
                "eventid": 1001,
                "name": "Tropical Cyclone BAVI-26",
                "fromdate": "2026-09-20T06:00:00",
                "todate": "2026-09-24T12:00:00",
                "alertlevel": "Orange",
                "country": "Taiwan",
                "affectedcountries": [
                    {"countryname": "Taiwan"},
                    {"countryname": "China"},
                ],
                "severitydata": {"severity": 148.0},
                "url": {"report": "https://www.gdacs.org/report.aspx?eventid=1001"},
            },
        },
        {
            # starts after the information cutoff: a PIT report must not
            # be able to see it
            "geometry": {"coordinates": [140.0, 20.0]},
            "properties": {
                "eventtype": "TC",
                "eventid": 1002,
                "name": "Tropical Cyclone CLARA-26",
                "fromdate": "2026-10-01T00:00:00",
                "todate": "2026-10-04T00:00:00",
                "alertlevel": "Red",
                "severitydata": {"severity": 200.0},
            },
        },
        {
            # not a hazard type we model
            "geometry": {"coordinates": [10.0, 45.0]},
            "properties": {
                "eventtype": "XX",
                "eventid": 1003,
                "name": "mystery",
                "fromdate": "2026-09-20T00:00:00",
            },
        },
    ]
}

USGS_FEED = {
    "features": [
        {
            "geometry": {"coordinates": [-70.1, -33.2]},
            "properties": {
                "mag": 6.8,
                "place": "40 km W of Valparaiso, Chile",
                "time": QUAKE_MS,
                "tsunami": 1,
                "alert": "orange",
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us123",
                "ids": ",us123,",
            },
        },
        {
            "geometry": {"coordinates": [10.0, 45.0]},
            "properties": {
                "mag": 5.1,
                "place": "northern Italy",
                "time": QUAKE_MS,
                "tsunami": 0,
            },
        },
    ]
}

NOAA_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
  <entry>
    <title>Tsunami Warning - Chile</title>
    <updated>2026-09-20T07:30:00Z</updated>
    <link rel="alternate" href="https://www.tsunami.gov/bulletin/1"/>
    <geo:lat>-33.2</geo:lat>
    <geo:long>-70.1</geo:long>
    <content type="html">&lt;strong&gt;Category:&lt;/strong&gt; Warning
      &lt;strong&gt;Magnitude:&lt;/strong&gt; 6.8</content>
  </entry>
  <entry>
    <title>Tsunami Information Statement - Aleutians</title>
    <updated>2026-09-10T01:00:00Z</updated>
    <content type="html">&lt;strong&gt;Category:&lt;/strong&gt; Information</content>
  </entry>
</feed>
"""

CUTOFF = sources._day_end_ts("2026-09-24")


# --- GDACS ------------------------------------------------------------------


def test_gdacs_signals_map_onto_the_severity_vocabulary() -> None:
    signals = sources._parse_gdacs(GDACS_TC, CUTOFF)
    assert len(signals) == 1
    tc = signals[0]
    # the hint must exist in both scoring tables or the hazard scores zero
    assert tc.kind_hint in config.KIND_BASE_SEVERITY
    assert tc.kind_hint in config.HINT_TO_MECHANISMS
    assert tc.kind_hint == "extreme_weather"
    assert tc.source_name == "GDACS (TC)"
    assert tc.credibility == 0.9  # Orange alert
    assert "148 km/h winds" in tc.text
    assert "Taiwan" in tc.text and "China" in tc.text
    assert tc.geo == (24.0, 121.0)


def test_gdacs_respects_the_point_in_time_cutoff() -> None:
    # the storm that starts in October is invisible as of September 24
    assert [s.text for s in sources._parse_gdacs(GDACS_TC, CUTOFF)] == [
        s.text for s in sources._parse_gdacs(GDACS_TC, CUTOFF) if "BAVI" in s.text
    ]
    later = sources._day_end_ts("2026-10-05")
    assert len(sources._parse_gdacs(GDACS_TC, later)) == 2


# --- USGS -------------------------------------------------------------------


def test_usgs_keeps_only_tsunami_flagged_quakes() -> None:
    signals = sources._parse_usgs(USGS_FEED, sources._day_end_ts("2026-09-24"))
    assert len(signals) == 1
    quake = signals[0]
    assert quake.kind_hint == "tsunami"
    assert quake.kind_hint in config.KIND_BASE_SEVERITY
    assert "M6.8" in quake.text
    assert "tsunami potential flagged" in quake.text
    assert "Orange" in quake.text
    assert quake.geo == (-33.2, -70.1)


def test_usgs_drops_quakes_after_the_cutoff() -> None:
    assert sources._parse_usgs(USGS_FEED, sources._day_end_ts("2026-09-01")) == []


# --- NOAA -------------------------------------------------------------------


def test_noaa_bulletins_parse_category_and_geometry() -> None:
    signals = sources._parse_noaa_tsunami(
        NOAA_ATOM, sources._day_end_ts("2026-09-24"), "PTWC (Hawaii)"
    )
    assert len(signals) == 2
    warning = signals[0]
    assert warning.kind_hint == "tsunami"
    assert warning.credibility == 0.95  # a Warning is as certain as it gets
    assert "Tsunami warning" in warning.text
    assert "[NOAA PTWC (Hawaii)]" in warning.text
    assert warning.geo == (-33.2, -70.1)
    assert warning.url == "https://www.tsunami.gov/bulletin/1"
    # an Information Statement stays low-confidence but is still a signal
    assert signals[1].credibility == 0.8


def test_noaa_bulletins_apply_the_cutoff() -> None:
    cutoff = sources._day_end_ts("2026-09-15")
    signals = sources._parse_noaa_tsunami(NOAA_ATOM, cutoff, "NTWC (Alaska)")
    assert [s.ts <= cutoff for s in signals] == [True]


def test_noaa_handles_a_malformed_feed() -> None:
    assert sources._parse_noaa_tsunami("<not-xml", CUTOFF, "NTWC (Alaska)") == []


# --- anchoring --------------------------------------------------------------


def test_hazards_anchor_to_the_nearest_supply_node() -> None:
    text, entities = sources.anchor_to_supply_node(
        24.5, 121.0, "Typhoon BAVI-26", ()
    )
    assert "nearest supply node:" in text
    assert entities  # node words become cluster entities
    # far out in the Pacific there is nothing to anchor to
    bare, kept = sources.anchor_to_supply_node(0.0, -140.0, "Typhoon X", ("typhoon",))
    assert bare == "Typhoon X"
    assert kept == ("typhoon",)


# --- de-duplication ---------------------------------------------------------


def _signal(**kwargs) -> Signal:
    base = {
        "ts": BULLETIN_TS,
        "source_type": "govt_advisory",
        "source_name": "X",
        "url": "",
        "text": "",
        "geo": (-33.2, -70.1),
        "entities": (),
        "kind_hint": "tsunami",
        "credibility": 0.9,
        "signal_id": "id",
    }
    return Signal(**{**base, **kwargs})


def test_dedupe_drops_a_quake_noaa_already_bulletined() -> None:
    bulletins = [
        _signal(source_name="NOAA PTWC (Hawaii)", text="Tsunami warning - Chile")
    ]
    same_quake = _signal(
        source_name="USGS Earthquake Hazards Program",
        ts=BULLETIN_TS - 1800,
        geo=(-33.25, -70.15),
        text="M6.8 earthquake",
    )
    # a different quake half a world away is its own signal
    other_quake = _signal(
        source_name="USGS Earthquake Hazards Program",
        geo=(35.6, 140.1),
        text="M6.1 earthquake",
        signal_id="id2",
    )
    kept = sources._dedupe_hazards([*bulletins, same_quake, other_quake])
    assert [s.text for s in kept] == [
        "Tsunami warning - Chile",
        "M6.1 earthquake",
    ]


def test_dedupe_drops_an_eonet_storm_gdacs_already_numbers() -> None:
    gdacs = _signal(
        source_name="GDACS (TC)",
        text="Tropical Cyclone BAVI-26 (Taiwan)",
        kind_hint="extreme_weather",
    )
    eonet = _signal(
        source_name="NASA EONET",
        text="Typhoon Bavi (nearest supply node: TSMC Hsinchu, 80km)",
        kind_hint="extreme_weather",
        signal_id="id2",
    )
    unrelated = _signal(
        source_name="NASA EONET",
        text="Typhoon Mitag",
        kind_hint="extreme_weather",
        signal_id="id3",
    )
    kept = sources._dedupe_hazards([gdacs, eonet, unrelated])
    assert [s.signal_id for s in kept] == [gdacs.signal_id, unrelated.signal_id]


def test_storm_name_extraction() -> None:
    assert sources._storm_name("Tropical Cyclone BAVI-26 (Taiwan)") == "bavi"
    assert sources._storm_name("Typhoon Bavi") == "bavi"
    assert sources._storm_name("M6.8 earthquake") == ""


# --- scout resilience -------------------------------------------------------


def test_hazard_signals_survives_dead_feeds(monkeypatch) -> None:
    def dead(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(sources, "_fetch", dead)
    assert list(sources.hazard_signals("2026-09-14", "2026-09-24")) == []


def test_hazard_signals_combines_and_dedupes(monkeypatch) -> None:
    def fake(url, _params=None, **_kwargs):
        class Response:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

            @property
            def text(self):
                return self._payload

        if "gdacs" in url:
            return Response(GDACS_TC)
        if "usgs" in url:
            return Response(USGS_FEED)
        return Response(NOAA_ATOM)

    monkeypatch.setattr(sources, "_fetch", fake)
    signals = list(sources.hazard_signals("2026-09-14", "2026-09-24"))
    names = [s.source_name for s in signals]
    # the Chilean quake is one event, reported once, by NOAA
    assert not any(n.startswith("USGS") for n in names)
    assert any(n.startswith("GDACS") for n in names)
    assert any(n.startswith("NOAA") for n in names)
    # sorted for replay-style consumption
    assert signals == sorted(signals, key=lambda s: s.ts)


@pytest.mark.parametrize(
    ("day", "expected_hour"),
    [("2026-09-24", 23), ("2026-09-24T08:00:00Z", 8)],
)
def test_cutoff_uses_whole_days_for_bare_dates(day, expected_hour) -> None:
    cutoff = datetime.fromtimestamp(sources._day_end_ts(day), UTC)
    assert cutoff.hour == expected_hour
