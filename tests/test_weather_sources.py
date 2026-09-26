"""Tests for the keyless weather scouts: Open-Meteo, GloFAS, NHC and NWS.

The collectors are driven with canned payloads shaped like the real
responses, so the suite stays offline and deterministic while covering the
decisions that only fail in production: the threshold gate, the PIT live
window, the "this cell carries no water" gate that produced bogus low-water
signals for canals and seas, and the forecast/observation split the report
renders differently.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from watchtower import config, sources
from watchtower.models import Chokepoint, Signal
from watchtower.report import early_indicator_rows, is_forecast

TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _no_cache():
    """A cached response would leak between tests."""
    sources.clear_cache()
    yield
    sources.clear_cache()


def _node(node_id="n1", name="Test Port", kind="port", geo=(24.0, 121.0)):
    return Chokepoint(
        node_id=node_id,
        name=name,
        kind=kind,
        geo=geo,
        criticality=0.5,
    )


class Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._payload


def _days(*offsets: int) -> list[str]:
    """ISO day strings relative to today."""
    base = date.fromisoformat(TODAY)
    return [(base + timedelta(days=offset)).isoformat() for offset in offsets]


def _forecast_daily(metric: str, values: list[float], offsets: list[int]) -> dict:
    return {"daily": {"time": _days(*offsets), metric: values}}


# --- threshold gate ---------------------------------------------------------


def test_forecast_emits_nothing_below_the_threshold(monkeypatch) -> None:
    payload = _forecast_daily("wind_speed_10m_max", [40.0, 55.0, 60.0], [0, 1, 2])
    monkeypatch.setattr(sources, "_fetch", lambda *a, **k: Response(payload))
    assert list(sources.openmeteo_forecast_signals([_node()], TODAY)) == []


def test_forecast_emits_one_signal_at_the_threshold(monkeypatch) -> None:
    payload = _forecast_daily("wind_speed_10m_max", [40.0, 90.0, 60.0], [0, 1, 2])
    monkeypatch.setattr(sources, "_fetch", lambda *a, **k: Response(payload))
    signals = list(sources.openmeteo_forecast_signals([_node()], TODAY))
    assert len(signals) == 1
    signal = signals[0]
    assert signal.source_name == "Open-Meteo"
    assert signal.kind_hint == "extreme_weather"
    assert is_forecast(signal)
    # the day it happens and the lead time are both in the text
    assert "(+1d)" in signal.text
    assert "90 km/h" in signal.text
    assert signal.credibility == sources.FORECAST_CREDIBILITY


def test_forecast_worst_day_wins(monkeypatch) -> None:
    # a single monster day among calm ones, out of order
    payload = _forecast_daily("wind_speed_10m_max", [95.0, 20.0, 30.0], [1, 0, 2])
    monkeypatch.setattr(sources, "_fetch", lambda *a, **k: Response(payload))
    signals = list(sources.openmeteo_forecast_signals([_node()], TODAY))
    assert len(signals) == 1
    assert "95 km/h" in signals[0].text
    assert "(+1d)" in signals[0].text  # the peak day, not today


def test_cold_direction_words_the_forecast_downwards(monkeypatch) -> None:
    payload = _forecast_daily("temperature_2m_min", [5.0, -20.0, -4.0], [0, 1, 2])
    monkeypatch.setattr(sources, "_fetch", lambda *a, **k: Response(payload))
    signals = list(sources.openmeteo_forecast_signals([_node()], TODAY))
    assert len(signals) == 1
    assert "down to" in signals[0].text
    assert "extreme cold" in signals[0].text


def test_forecast_uses_the_archive_for_a_past_cutoff(monkeypatch) -> None:
    seen: list[str] = []

    def fake(url, _params=None, **_kwargs):
        seen.append(url)
        return Response(_forecast_daily("wind_speed_10m_max", [95.0], [0]))

    monkeypatch.setattr(sources, "_fetch", fake)
    signals = list(sources.openmeteo_forecast_signals([_node()], "2026-01-05"))
    assert seen == [sources.OPEN_METEO_ARCHIVE]
    assert len(signals) == 1


# --- previous-run backtesting -----------------------------------------------


def _previous_payload(var: str, day_offset: int, value: float, leads: int) -> dict:
    """Hourly payload where ``var`` is hot only on the day at ``day_offset``."""
    base = date.fromisoformat(TODAY)
    times: list[str] = []
    series: list[float] = []
    for offset in range(leads + 1):
        day = (base + timedelta(days=offset)).isoformat()
        for hour in range(24):
            times.append(f"{day}T{hour:02d}:00")
            series.append(value if offset == day_offset else 0.0)
    return {"hourly": {"time": times, var: series}}


def test_previous_run_uses_the_exact_lead_run(monkeypatch) -> None:
    seen: list[dict] = []

    def fake(_url, params=None, **_kwargs):
        seen.append(params)
        # the 2-day-ahead run saw 95 km/h on the day two ahead
        return Response(
            _previous_payload("wind_speed_10m_previous_day2", 2, 95.0, leads=2)
        )

    monkeypatch.setattr(sources, "_fetch", fake)
    signals = list(
        sources.openmeteo_previous_run_signals([_node()], TODAY, lead_days=2)
    )
    assert len(signals) == 1
    signal = signals[0]
    assert "(+2d)" in signal.text
    assert "previous runs" in signal.text
    assert "95 km/h" in signal.text
    # a date range, not a live window: the run is reconstructed, not guessed
    assert seen and seen[0]["start_date"] == TODAY
    assert seen[0]["end_date"] == _days(2)[0]


def test_previous_run_respects_the_threshold(monkeypatch) -> None:
    monkeypatch.setattr(
        sources,
        "_fetch",
        lambda *a, **k: Response(
            _previous_payload("wind_speed_10m_previous_day1", 1, 10.0, leads=1)
        ),
    )
    assert (
        list(sources.openmeteo_previous_run_signals([_node()], TODAY, lead_days=1))
        == []
    )


def test_previous_run_folds_hours_into_daily_metrics(monkeypatch) -> None:
    # 3 mm/hour every hour -> a 72 mm day, just under the 80 mm threshold
    payload = _previous_payload("precipitation_previous_day1", 1, 3.0, leads=1)
    monkeypatch.setattr(sources, "_fetch", lambda *a, **k: Response(payload))
    assert (
        list(sources.openmeteo_previous_run_signals([_node()], TODAY, lead_days=1))
        == []
    )
    # 4 mm/hour -> 96 mm, over the line
    sources.clear_cache()  # the same request params would otherwise be reused
    payload = _previous_payload("precipitation_previous_day1", 1, 4.0, leads=1)
    monkeypatch.setattr(sources, "_fetch", lambda *a, **k: Response(payload))
    signals = list(sources.openmeteo_previous_run_signals([_node()], TODAY, lead_days=1))
    assert len(signals) == 1
    assert "96 mm" in signals[0].text
    assert signals[0].kind_hint == "flood"


def test_fold_max_min_sum() -> None:
    assert sources._fold([1.0, 5.0, 2.0], "max") == 5.0
    assert sources._fold([1.0, 5.0, 2.0], "min") == 1.0
    assert sources._fold([1.0, 5.0, 2.0], "sum") == 8.0
    assert sources._fold([], "max") is None
    assert sources._fold([None, 2.0], "max") == 2.0


# --- PIT live window --------------------------------------------------------


def test_live_only_refuses_a_past_cutoff() -> None:
    old = datetime(2026, 1, 5, tzinfo=UTC).timestamp()
    assert sources._live_only(old, "test") is False
    assert sources._live_only(datetime.now(UTC).timestamp(), "test") is True


def test_river_and_nhc_skip_a_past_cutoff(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("a live-only source must not be fetched")

    monkeypatch.setattr(sources, "_fetch", boom)
    assert list(sources.openmeteo_river_signals([_node(kind="canal")], "2026-01-01")) == []
    assert list(sources.nhc_cyclone_signals("2026-01-01")) == []
    assert list(sources.nws_alert_signals("2026-01-01")) == []


# --- GloFAS water gate ------------------------------------------------------


def _river_payload(values: list[float], offsets: list[int]) -> dict:
    return {"daily": {"time": _days(*offsets), "river_discharge": values}}


def test_river_gate_drops_a_flat_zero_canal(monkeypatch) -> None:
    # GloFAS answers for a canal with a flat ~0 series, which passes any
    # percentile test: the gate must reject it.
    offsets = list(range(-30, 1))
    monkeypatch.setattr(
        sources, "_fetch", lambda *a, **k: Response(_river_payload([0.0] * 31, offsets))
    )
    signals = list(sources.openmeteo_river_signals([_node(kind="canal")], TODAY))
    assert signals == []


def test_river_signals_flag_low_water_against_the_baseline(monkeypatch) -> None:
    offsets = list(range(-30, 1))
    baseline = [100.0 + i for i in range(30)]  # 100..129
    monkeypatch.setattr(
        sources,
        "_fetch",
        lambda *a, **k: Response(_river_payload([*baseline, 2.0], offsets)),
    )
    signals = list(sources.openmeteo_river_signals([_node(kind="canal")], TODAY))
    assert len(signals) == 1
    signal = signals[0]
    assert signal.source_name == "GloFAS"
    assert signal.kind_hint == "drought"
    assert is_forecast(signal)
    assert "river discharge falling to 2.0 m3/s" in signal.text


def test_river_ignores_nodes_whose_level_does_not_matter(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("a fab node is not a river node")

    monkeypatch.setattr(sources, "_fetch", boom)
    assert list(sources.openmeteo_river_signals([_node(kind="fab")], TODAY)) == []


def test_river_skips_a_baseline_too_short_to_judge(monkeypatch) -> None:
    monkeypatch.setattr(
        sources,
        "_fetch",
        lambda *a, **k: Response(_river_payload([100.0] * 5 + [2.0], [-5, -4, -3, -2, -1, 0])),
    )
    assert list(sources.openmeteo_river_signals([_node(kind="canal")], TODAY)) == []


# --- helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "percent", "expected"),
    [([1, 2, 3, 4, 5], 0, 1), ([1, 2, 3, 4, 5], 100, 5), ([1, 2, 3, 4, 5], 50, 3)],
)
def test_percentile_nearest_rank(values, percent, expected) -> None:
    assert sources._percentile([float(v) for v in values], percent) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("29.9N", 29.9), ("43.4W", -43.4), ("12.0S", -12.0), ("121.0E", 121.0), ("oops", None)],
)
def test_coord_parses_nhc_hemispheres(raw, expected) -> None:
    assert sources._coord(raw) == expected


@pytest.mark.parametrize(
    ("event", "hint"),
    [
        ("Hurricane Warning", "extreme_weather"),
        ("Flash Flood Warning", "flood"),
        ("Red Flag Warning", "fire"),
        ("Drought Advisory", "drought"),
        ("Frost Advisory", "extreme_weather"),
        ("Air Quality Alert", None),
    ],
)
def test_nws_hint_maps_only_modelled_hazards(event, hint) -> None:
    assert sources._nws_hint(event) == hint


def test_alert_geo_reads_polygon_and_multipolygon() -> None:
    polygon = {"geometry": {"type": "Polygon", "coordinates": [[[-121.0, 24.0], [0, 0]]]}}
    multi = {
        "geometry": {"type": "MultiPolygon", "coordinates": [[[[-121.0, 24.0]]]]}
    }
    assert sources._alert_geo(polygon) == (24.0, -121.0)
    assert sources._alert_geo(multi) == (24.0, -121.0)
    assert sources._alert_geo({"geometry": None}) is None


# --- NHC and NWS collectors -------------------------------------------------


def test_nhc_names_and_positions_a_storm(monkeypatch) -> None:
    payload = {
        "activeStorms": [
            {
                "id": "al0926",
                "name": "GABRIELLE",
                "classification": "HU",
                "intensity": "90",
                "pressure": "970",
                "latitude": "29.9N",
                "longitude": "43.4W",
                "url": "https://www.nhc.noaa.gov/storm",
            }
        ]
    }
    monkeypatch.setattr(sources, "_fetch", lambda *a, **k: Response(payload))
    signals = list(sources.nhc_cyclone_signals(TODAY))
    assert len(signals) == 1
    signal = signals[0]
    assert signal.source_name == "NHC"
    assert "Hurricane GABRIELLE" in signal.text
    assert signal.geo == (29.9, -43.4)
    assert signal.credibility == 0.9


def test_nws_keeps_modelled_alerts_and_caps_them(monkeypatch) -> None:
    payload = {
        "features": [
            {
                "geometry": {"type": "Polygon", "coordinates": [[[-112.0, 33.4]]]},
                "properties": {
                    "id": "alert-1",
                    "event": "Excessive Heat Warning",
                    "severity": "Severe",
                    "headline": "Excessive Heat Warning for Phoenix",
                    "areaDesc": "Maricopa; Pinal",
                },
            },
            {
                "geometry": None,
                "properties": {
                    "id": "alert-2",
                    "event": "Air Quality Alert",
                    "severity": "Minor",
                    "areaDesc": "Somewhere",
                },
            },
        ]
    }
    monkeypatch.setattr(sources, "_fetch", lambda *a, **k: Response(payload))
    signals = list(sources.nws_alert_signals(TODAY))
    assert len(signals) == 1  # the air-quality alert is not a hazard we model
    signal = signals[0]
    assert signal.kind_hint == "extreme_weather"
    assert "[Maricopa]" in signal.text  # only the first area
    assert signal.geo == (33.4, -112.0)


# --- aggregation and resilience ---------------------------------------------


def test_weather_signals_survives_dead_sources(monkeypatch) -> None:
    def dead(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(sources, "_fetch", dead)
    assert list(sources.weather_signals([_node()], TODAY)) == []


def test_weather_signals_combines_sources(monkeypatch) -> None:
    def fake(url, _params=None, **_kwargs):
        if "flood" in url:
            offsets = list(range(-30, 1))
            return Response(
                _river_payload([100.0 + i for i in range(30)] + [2.0], offsets)
            )
        if url == sources.NHC_STORMS:
            return Response(
                {
                    "activeStorms": [
                        {
                            "id": "al1",
                            "name": "GABRIELLE",
                            "classification": "HU",
                            "latitude": "29.9N",
                            "longitude": "43.4W",
                        }
                    ]
                }
            )
        if url == sources.NWS_ALERTS:
            return Response(
                {
                    "features": [
                        {
                            "geometry": {"type": "Polygon", "coordinates": [[[-112.0, 33.4]]]},
                            "properties": {
                                "id": "a1",
                                "event": "Flash Flood Warning",
                                "severity": "Severe",
                                "areaDesc": "Maricopa",
                            },
                        }
                    ]
                }
            )
        return Response(_forecast_daily("wind_speed_10m_max", [95.0], [0]))

    monkeypatch.setattr(sources, "_fetch", fake)
    signals = list(sources.weather_signals([_node(kind="canal")], TODAY))
    names = {s.source_name for s in signals}
    assert names == {"Open-Meteo", "GloFAS", "NHC", "NWS"}
    assert signals == sorted(signals, key=lambda s: s.ts)


# --- forecast/observation split ---------------------------------------------


def test_early_indicator_rows_lists_only_forecasts() -> None:
    observed = Signal(
        ts=1.0,
        source_type="analyst",
        source_name="X",
        url="",
        text="port closure observed",
        geo=None,
        entities=(),
        kind_hint="port",
        credibility=0.9,
    )
    forecast = Signal(
        ts=2.0,
        source_type="analyst",
        source_name="Open-Meteo",
        url="",
        text="forecast: sustained winds up to 95 km/h at Test Port on 2026-09-26 (+2d)",
        geo=None,
        entities=(),
        kind_hint="extreme_weather",
        credibility=0.55,
    )
    assert is_forecast(observed) is False
    assert is_forecast(forecast) is True
    rows = early_indicator_rows([observed, forecast])
    assert len(rows) == 3  # header, separator, one forecast
    assert "+2d" in rows[2]
    assert "Open-Meteo" in rows[2]
    assert early_indicator_rows([observed]) == []


# --- SITREP forecast selection ----------------------------------------------


def _sitrep_module():
    spec = importlib.util.spec_from_file_location(
        "sitrep_forecast_mod", Path("scripts/sitrep.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signal(text: str, geo, source_name: str = "Open-Meteo") -> Signal:
    return Signal(
        ts=1000.0,
        source_type="analyst",
        source_name=source_name,
        url="",
        text=text,
        geo=geo,
        entities=(),
        kind_hint="extreme_weather",
        credibility=0.55,
    )


def test_nearest_forecasts_ranks_by_distance_and_excludes_observations() -> None:
    module = _sitrep_module()
    near = _signal("forecast: winds up to 95 km/h at Near on 2026-09-27 (+1d)", (24.2, 121.1))
    far = _signal("forecast: winds up to 95 km/h at Far on 2026-09-27 (+1d)", (40.0, -75.0))
    observed = _signal("port closure observed", (24.0, 121.0), source_name="NWS")
    event = SimpleNamespace(centroid=(24.0, 121.0), signals=[observed])
    picked = module._nearest_forecasts(event, [far, near, observed], limit=12)
    assert [s.text.split(" at ")[1] for s in picked] == [
        "Near on 2026-09-27 (+1d)",
        "Far on 2026-09-27 (+1d)",
    ]
    assert module._nearest_forecasts(event, [observed]) == []


def test_nearest_forecasts_respects_the_limit() -> None:
    module = _sitrep_module()
    forecasts = [
        _signal(f"forecast: winds up to 95 km/h at N{i} on 2026-09-27 (+1d)", (24.0, 121.0))
        for i in range(20)
    ]
    event = SimpleNamespace(centroid=(24.0, 121.0), signals=[])
    assert len(module._nearest_forecasts(event, forecasts, limit=5)) == 5


# --- config -----------------------------------------------------------------


def test_weather_yaml_loads_with_sane_thresholds() -> None:
    spec = config.load_weather()
    assert spec.horizon_days >= 1
    assert spec.river_baseline_days >= sources._MIN_BASELINE_DAYS
    assert 0 < spec.river_low_percentile < 50
    assert spec.river_min_discharge > 0
    assert 0 < spec.thresholds.wind_kmh <= spec.thresholds.gust_kmh
    assert spec.thresholds.cold_c < 0 < spec.thresholds.heat_c
    limits = sources._thresholds(spec)
    assert limits["wind_speed_10m_max"] == spec.thresholds.wind_kmh
    assert limits["temperature_2m_min"] == spec.thresholds.cold_c
