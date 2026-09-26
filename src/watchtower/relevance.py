"""Lexical relevance: TF cosine between event text and a market context doc.

This is the single canonical implementation of the "relevance to <market>"
score used in the SITREPs. It replaces three copies that had drifted apart
(the d-dev ``scripts/sitrep.py`` helpers, the ``scoring.py`` port, and the
ad-hoc docs built in the agent correlator), so a number in a report can
always be reproduced from the live config.

The score is deliberately simple and auditable rather than clever:

* **document** = one signal's ``text`` plus its extracted ``entities`` —
  entities carry the place, node and asset names, and dropping them
  deflates the score roughly threefold, so their inclusion is part of the
  definition, not an implementation detail.
* **context document** = the market's watchlist, node kinds, mechanism
  vocabulary, label and a per-kind canonical vocabulary. Built from
  ``markets.yaml`` so it tracks the config instead of a hardcoded list.
* **aggregate** = credibility-weighted mean cosine over the ten most
  credible signals, scaled by :data:`RELEVANCE_SCALE` and capped at 1.

Validation notes (see ``tests/test_relevance.py``):

* ``cosine`` matches a reference dot-product/norm implementation exactly,
  is symmetric, returns 1 for a document against itself and 0 when either
  side is empty.
* On the replay scenarios raw values land in 0.008-0.104, i.e. the x3
  scale maps them to 0.02-0.31 and the cap is never reached. Treat the
  output as an ordinal lexical-overlap score, **not** a calibrated 0-1
  probability — M7.8 events with no industry vocabulary in their text
  score ~0 no matter how close they are to a fab. Exposure, not this
  score, is what makes an event material; use both.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from watchtower.models import EventCluster
from watchtower.config import MarketSpec

__all__ = [
    "RELEVANCE_SCALE",
    "TOP_SIGNALS",
    "cosine",
    "context_doc",
    "lexical_relevance",
    "raw_relevance",
    "relevance_audit",
    "tokenize",
]

#: Display scale. Raw term-frequency cosine is small (observed max ~0.10 on
#: the replay corpus); x3 lifts it into a readable range. It is not a
#: probability calibration.
RELEVANCE_SCALE = 3.0

#: Signals aggregated into the score (the most credible ones).
TOP_SIGNALS = 10

# Canonical per-node-kind vocabulary, so an event that says "foundry" or
# "wafer" matches a fab-centric market even without naming a ticker.
NODE_VOCAB = {
    "fab": "semiconductor fab chip wafer foundry",
    "osat": "assembly test packaging",
    "material": "neon quartz substrate palladium gas",
    "canal": "canal shipping container vessel transit",
    "strait": "strait shipping tanker vessel transit",
    "port": "port cargo container shipping freight",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens (matches the historical behaviour)."""
    return _TOKEN_RE.findall(text.lower())


def cosine(a: Counter, b: Counter) -> float:
    """Cosine similarity of two term-frequency vectors, 0 when either is empty."""
    num = sum(a[token] * b.get(token, 0) for token in a)
    da = math.sqrt(sum(value * value for value in a.values()))
    db = math.sqrt(sum(value * value for value in b.values()))
    return num / (da * db) if da and db else 0.0


@lru_cache(maxsize=16)
def context_doc(market: MarketSpec) -> Counter:
    """Market-context term vector, derived from the market spec."""
    terms = [
        *market.watchlist,
        *sorted(market.node_kinds),
        *sorted(market.mechanisms),
        *market.label.split(),
    ]
    for kind in market.node_kinds:
        terms += NODE_VOCAB.get(kind, "").split()
    return Counter(tokenize(" ".join(terms)))


def _document(signal) -> Counter:
    """One signal as a term vector: text plus entities."""
    return Counter(tokenize(f"{signal.text} {' '.join(signal.entities)}"))


def _top_signals(event: EventCluster, limit: int):
    return sorted(event.signals, key=lambda s: s.credibility, reverse=True)[:limit]


def raw_relevance(
    event: EventCluster, market: MarketSpec, *, limit: int = TOP_SIGNALS
) -> float:
    """Unscaled credibility-weighted mean cosine, in [0, 1]."""
    top = _top_signals(event, limit)
    if not top:
        return 0.0
    ctx = context_doc(market)
    sims = [cosine(_document(s), ctx) for s in top]
    weight = sum(s.credibility for s in top) or 1.0
    return sum(sim * s.credibility for sim, s in zip(sims, top)) / weight


def lexical_relevance(
    event: EventCluster, market: MarketSpec, *, limit: int = TOP_SIGNALS
) -> float:
    """Scaled and capped relevance, rounded to 2dp (what the reports print)."""
    return round(min(1.0, raw_relevance(event, market, limit=limit) * RELEVANCE_SCALE), 2)


def relevance_audit(event: EventCluster, market: MarketSpec) -> dict:
    """Per-signal breakdown, so a printed score can be reconstructed.

    ``matched`` lists the context tokens each signal actually hit, which is
    how accidental overlaps (a stray "on" or "fire") become visible instead
    of silently inflating the score.
    """
    ctx = context_doc(market)
    top = _top_signals(event, TOP_SIGNALS)
    weight = sum(s.credibility for s in top) or 1.0
    rows = []
    for signal in top:
        doc = _document(signal)
        rows.append(
            {
                "source": signal.source_name,
                "credibility": round(signal.credibility, 2),
                "cosine": round(cosine(doc, ctx), 4),
                "matched": sorted(token for token in doc if token in ctx),
            }
        )
    raw = sum(row["cosine"] * row["credibility"] for row in rows) / weight
    return {
        "market": market.key,
        "signals_total": len(event.signals),
        "signals_scored": len(top),
        "raw": round(raw, 4),
        "scaled": lexical_relevance(event, market),
        "cap_reached": raw * RELEVANCE_SCALE > 1.0,
        "rows": rows,
    }
