"""Validation tests for the lexical relevance score.

The score drives the "Relevance to <market>" line in every SITREP, so these
tests pin both the mathematics and the definition:

* the cosine matches a reference implementation and behaves like a cosine;
* the aggregate is credibility-weighted, entity-aware and bounded;
* the printed number is reproducible end-to-end from a replay scenario;
* the audit breakdown reconciles with the printed score (so a skeptical
  reader can reconstruct it, and accidental token overlaps are visible).
"""

from __future__ import annotations

import importlib.util
import math
import random
from collections import Counter
from pathlib import Path

import pytest

from watchtower import config
from watchtower.agents.orchestrator import Orchestrator
from watchtower.config import REPLAY_DIR, SCENARIO_EXCLUSIONS
from watchtower.models import EventCluster, Signal
from watchtower.relevance import (
    RELEVANCE_SCALE,
    TOP_SIGNALS,
    context_doc,
    cosine,
    lexical_relevance,
    raw_relevance,
    relevance_audit,
    tokenize,
)
from watchtower.sources import replay_signals

MARKET = config.load_markets()["semicon"]


def _vec(counter: Counter) -> tuple[Counter, float]:
    norm = math.sqrt(sum(v * v for v in counter.values()))
    return counter, norm


def _reference_cosine(a: Counter, b: Counter) -> float:
    keys = set(a) | set(b)
    num = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    _, da = _vec(a)
    _, db = _vec(b)
    return num / (da * db) if da and db else 0.0


def _random_counter(rng: random.Random) -> Counter:
    return Counter({f"t{i}": rng.randint(1, 5) for i in range(rng.randint(1, 20))})


# --- the math ---------------------------------------------------------------


def test_cosine_matches_reference_implementation() -> None:
    rng = random.Random(0)
    for _ in range(2000):
        a, b = _random_counter(rng), _random_counter(rng)
        assert cosine(a, b) == pytest.approx(_reference_cosine(a, b), abs=1e-12)


def test_cosine_is_a_cosine() -> None:
    rng = random.Random(1)
    for _ in range(200):
        a, b = _random_counter(rng), _random_counter(rng)
        assert 0.0 <= cosine(a, b) <= 1.0
        assert cosine(a, b) == pytest.approx(cosine(b, a), abs=1e-12)
    assert cosine(Counter({"a": 3}), Counter({"a": 3})) == pytest.approx(1.0)
    # disjoint documents and empty documents both score zero, never NaN
    assert cosine(Counter({"a": 1}), Counter({"b": 1})) == 0.0
    assert cosine(Counter(), Counter({"a": 1})) == 0.0
    assert cosine(Counter(), Counter()) == 0.0


def test_cosine_is_scale_invariant() -> None:
    """Cosine normalises term frequency, so duplicating a doc changes nothing."""
    doc = Counter({"canal": 2, "shipping": 1})
    ctx = context_doc(MARKET)
    assert cosine(doc, ctx) == pytest.approx(
        cosine(Counter({k: v * 7 for k, v in doc.items()}), ctx), abs=1e-12
    )


def test_tokenize_is_lowercase_alphanumeric() -> None:
    assert tokenize("TSMC's Fab-18 (2024)!") == ["tsmc", "s", "fab", "18", "2024"]


# --- the context document ---------------------------------------------------


def test_context_doc_is_derived_from_the_market_spec() -> None:
    doc = context_doc(MARKET)
    # every watchlist ticker and node kind must be represented, lowercased
    for token in MARKET.watchlist:
        assert token.lower() in doc
    assert set(MARKET.node_kinds) <= set(doc)
    assert "canal" in doc and "shipping" in doc  # per-kind canonical vocabulary
    # a different market yields a different context
    assert context_doc(config.load_markets()["semicon"]) is doc  # cached, stable


# --- the aggregate ----------------------------------------------------------


def _event(*signals: Signal) -> EventCluster:
    return EventCluster(
        event_id="EVT-TEST", title="test", signals=list(signals), last_seen=0.0
    )


def _signal(text: str, *, credibility: float = 0.9, entities=()) -> Signal:
    return Signal(
        ts=0.0,
        source_type="wire",
        source_name="test",
        url="",
        text=text,
        entities=tuple(entities),
        credibility=credibility,
    )


def test_relevant_text_outscores_irrelevant_text() -> None:
    relevant = _event(_signal("Canal transit suspended, container vessels queue"))
    irrelevant = _event(_signal("Celebrity wedding photos published online"))
    assert lexical_relevance(relevant, MARKET) > lexical_relevance(irrelevant, MARKET)
    assert lexical_relevance(irrelevant, MARKET) == 0.0


def test_entities_are_part_of_the_document() -> None:
    """Dropping entities changed the printed score roughly threefold, so the
    entity-aware document is the definition we lock in."""
    with_entities = _event(_signal("Operations halted", entities=["TSMC Fab 18"]))
    without = _event(_signal("Operations halted"))
    assert lexical_relevance(with_entities, MARKET) > lexical_relevance(without, MARKET)


def test_aggregate_is_credibility_weighted() -> None:
    strong = _event(_signal("canal shipping", credibility=0.95))
    weak = _event(_signal("canal shipping", credibility=0.1))
    # a single low-credibility signal is not discounted by the weighting alone
    # (it is a weighted mean), so pin the weighting directly:
    mixed = _event(
        _signal("canal shipping", credibility=0.95),
        _signal("irrelevant chatter", credibility=0.05),
    )
    ctx = context_doc(MARKET)
    expected = (
        cosine(Counter(tokenize("canal shipping")), ctx) * 0.95
        + cosine(Counter(tokenize("irrelevant chatter")), ctx) * 0.05
    ) / 1.0
    assert raw_relevance(mixed, MARKET) == pytest.approx(expected, abs=1e-12)
    # equal text, lower credibility -> not higher (weights only re-rank signals)
    assert lexical_relevance(weak, MARKET) <= lexical_relevance(strong, MARKET)


def test_only_the_most_credible_signals_are_scored() -> None:
    signals = [_signal(f"irrelevant {i}", credibility=0.1) for i in range(20)]
    signals.append(_signal("canal shipping container", credibility=0.99))
    event = _event(*signals)
    assert raw_relevance(event, MARKET) == pytest.approx(
        raw_relevance(
            _event(*sorted(signals, key=lambda s: s.credibility, reverse=True)[:TOP_SIGNALS]),
            MARKET,
        ),
        abs=1e-12,
    )


def test_score_is_bounded_and_scaled() -> None:
    event = _event(_signal("canal shipping container vessel transit"))
    raw = raw_relevance(event, MARKET)
    assert 0.0 <= raw <= 1.0
    assert lexical_relevance(event, MARKET) == pytest.approx(
        round(min(1.0, raw * RELEVANCE_SCALE), 2)
    )
    assert lexical_relevance(_event(), MARKET) == 0.0  # no signals


def test_audit_reconciles_with_the_printed_score() -> None:
    event = _event(
        _signal("canal transit restricted", credibility=0.95, entities=["Panama Canal"]),
        _signal("weather chatter", credibility=0.2),
    )
    audit = relevance_audit(event, MARKET)
    weight = sum(row["credibility"] for row in audit["rows"]) or 1.0
    recomputed = sum(row["cosine"] * row["credibility"] for row in audit["rows"]) / weight
    assert audit["raw"] == pytest.approx(recomputed, abs=1e-4)
    assert audit["scaled"] == lexical_relevance(event, MARKET)
    assert audit["signals_scored"] == len(audit["rows"])
    # matched terms are printed so accidental overlaps are visible
    assert all(isinstance(row["matched"], list) for row in audit["rows"])


# --- end to end on the replay corpus ----------------------------------------


def _replay(scenario: str) -> EventCluster:
    orch = Orchestrator(
        MARKET, exclude_analogs=SCENARIO_EXCLUSIONS.get(scenario, frozenset())
    )
    for signal in replay_signals(REPLAY_DIR / f"{scenario}.jsonl"):
        orch.process(signal)
    return max(orch.correlator.active_events(), key=lambda c: len(c.signals))


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        # Locks the numbers the archived SITREPs print, recomputed against the
        # live 30-ticker watchlist. A config change that moves relevance must
        # be visible here rather than silently restating the old figure.
        ("panama_drought_2023", 0.31),
        ("suez_2021", 0.28),
        ("turkey_syria_earthquake_2023", 0.02),
    ],
)
def test_replay_relevance_is_reproducible(scenario: str, expected: float) -> None:
    assert lexical_relevance(_replay(scenario), MARKET) == pytest.approx(expected)


def test_relevance_never_saturates_on_the_replay_corpus() -> None:
    """The x3 scale is a display transform: if a scenario ever clipped at 1.0
    the top of the range would stop carrying information."""
    for scenario in ("panama_drought_2023", "suez_2021"):
        assert relevance_audit(_replay(scenario), MARKET)["cap_reached"] is False


# --- the SITREP delegates to this module ------------------------------------


def test_sitrep_uses_the_canonical_implementation() -> None:
    """Guard against the score drifting back into a second copy."""
    spec = importlib.util.spec_from_file_location(
        "sitrep_mod", Path("scripts/sitrep.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    event = _replay("panama_drought_2023")
    assert module._cosine_relevance(event, "semicon") == lexical_relevance(event, MARKET)
    assert not hasattr(module, "_tokenize")
