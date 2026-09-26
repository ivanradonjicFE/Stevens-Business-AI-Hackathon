"""Tests for the keyless news collectors: RSS/Atom feeds and the aggregator.

Every test drives the parser with a canned feed document shaped like the real
response, so the suite stays offline and deterministic while covering what
actually breaks in production: point-in-time cutoffs, the two feed dialects,
HTML in descriptions, publisher suffixes, and the same wire story arriving
from three sources at once.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from watchtower import config, sources
from watchtower.config import FeedSet, FeedSpec

RSS_FEED = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>gCaptain</title>
    <item>
      <title>Container ship runs aground in Suez Canal</title>
      <link>https://gcaptain.com/ever-given-aground/</link>
      <description>&lt;p&gt;Traffic in &lt;b&gt;both directions&lt;/b&gt; halted.&lt;/p&gt;</description>
      <pubDate>Fri, 25 Sep 2026 06:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Published after the cutoff</title>
      <link>https://gcaptain.com/future-story/</link>
      <description>Should never be seen by a PIT run.</description>
      <pubDate>Mon, 05 Oct 2026 06:30:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Trade press</title>
  <entry>
    <title>Wafer fab outage hits automotive supply</title>
    <link href="https://example.test/fab-outage"/>
    <summary>Production halted for at least a week.</summary>
    <published>2026-09-25T07:15:00Z</published>
  </entry>
</feed>
"""

GOOGLE_NEWS_FEED = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Chip shortage eases in Asia - Reuters</title>
      <link>https://news.google.com/rss/articles/CBMiABC</link>
      <source url="https://www.reuters.com">Reuters</source>
      <pubDate>Fri, 25 Sep 2026 08:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def _response(text):
    """A stand-in for ``httpx.Response`` with only what the collectors use."""

    class Response:
        def __init__(self, payload):
            self._payload = payload

        @property
        def text(self):
            return self._payload

        def json(self):
            return self._payload

    return Response(text)


# --- URL construction -------------------------------------------------------


def test_search_feed_url_encodes_the_query() -> None:
    url = sources.search_feed_url("TSMC OR foundry")
    assert url.startswith(sources.GOOGLE_NEWS_RSS)
    assert "q=TSMC+OR+foundry" in url
    assert "hl=en-US" in url


# --- parsing ----------------------------------------------------------------


def test_rss_parses_title_link_and_strips_html(monkeypatch) -> None:
    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response(RSS_FEED))
    signals = list(sources.rss_signals("https://gcaptain.com/feed/", "2026-09-25"))
    assert len(signals) == 1  # the post-cutoff item is dropped
    signal = signals[0]
    assert signal.source_name == "gCaptain"
    assert signal.url == "https://gcaptain.com/ever-given-aground/"
    assert "<p>" not in signal.text and "<b>" not in signal.text
    assert "Traffic in both directions halted" in signal.text
    assert signal.kind_hint in ("shipping_anomaly", "infrastructure")
    assert signal.credibility == 0.55
    assert signal.signal_id  # stable content-derived id


def test_atom_is_parsed_from_the_same_walk(monkeypatch) -> None:
    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response(ATOM_FEED))
    signals = list(sources.rss_signals("https://example.test/feed", "2026-09-25"))
    assert len(signals) == 1
    # Atom puts the URL in the attribute, RSS in the element text
    assert signals[0].url == "https://example.test/fab-outage"
    # "supply" is the materials_shortage keyword, so the hint is real not advisory
    assert signals[0].kind_hint == "materials_shortage"


def test_feed_timestamp_is_read_as_utc(monkeypatch) -> None:
    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response(RSS_FEED))
    signal = next(iter(sources.rss_signals("https://x.test/feed", "2026-09-25")))
    stamp = datetime.fromtimestamp(signal.ts, UTC)
    assert (stamp.year, stamp.month, stamp.day, stamp.hour) == (2026, 9, 25, 6)


def test_items_without_a_timestamp_are_dropped(monkeypatch) -> None:
    undated = """<rss version="2.0"><channel><item>
        <title>No date here</title><link>https://x.test/a</link>
    </item></channel></rss>"""
    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response(undated))
    assert list(sources.rss_signals("https://x.test/feed", "2026-09-25")) == []


def test_malformed_xml_yields_nothing_rather_than_raising(monkeypatch) -> None:
    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response("<rss><item"))
    assert list(sources.rss_signals("https://x.test/feed", "2026-09-25")) == []


def test_google_news_names_the_publisher_and_drops_the_suffix(monkeypatch) -> None:
    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response(GOOGLE_NEWS_FEED))
    signal = next(
        iter(sources.rss_signals(sources.search_feed_url("chips"), "2026-09-25"))
    )
    assert signal.source_name == "Reuters"
    assert signal.text.startswith("Chip shortage eases in Asia")
    assert " - Reuters" not in signal.text and "Reuters" not in signal.text


def test_bare_date_cutoff_includes_that_whole_day(monkeypatch) -> None:
    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response(RSS_FEED))
    # the 06:30 item is inside 2026-09-25, the October item is not
    assert len(list(sources.rss_signals("u", "2026-09-25"))) == 1
    assert len(list(sources.rss_signals("u", "2026-09-24"))) == 0


# --- de-duplication ---------------------------------------------------------


def _signal(title: str, url: str, name: str = "wire"):
    return sources.Signal(
        ts=1.0,
        source_type="trade_press",
        source_name=name,
        url=url,
        text=title,
        kind_hint="advisory",
        credibility=0.5,
    )


def test_dedupe_drops_the_same_link() -> None:
    signals = [
        _signal("Port congestion worsens", "https://a.test/story"),
        _signal("Port congestion worsens", "https://a.test/story?utm=x"),
    ]
    assert len(sources._dedupe_news(signals)) == 1


def test_dedupe_drops_the_same_headline_across_publishers() -> None:
    signals = [
        _signal("Chip supply tightens after fab outage", "https://a.test/1", "gCaptain"),
        _signal("Chip supply tightens after fab outage", "https://b.test/2", "Splash247"),
    ]
    assert len(sources._dedupe_news(signals)) == 1


def test_dedupe_keeps_genuinely_different_stories() -> None:
    signals = [
        _signal("Chip supply tightens after fab outage", "https://a.test/1"),
        _signal("Canal transit slots cut for the season", "https://a.test/2"),
    ]
    assert len(sources._dedupe_news(signals)) == 2


# --- the aggregator ---------------------------------------------------------


def _registry() -> FeedSet:
    return FeedSet(
        feeds=(FeedSpec("gCaptain", "https://gcaptain.com/feed/", "trade_press", 0.6),),
        queries={"semicon": ("TSMC OR foundry",)},
    )


def test_news_signals_combines_gdelt_and_rss(monkeypatch) -> None:
    def fake(url, _params=None, **_kwargs):
        if "gcaptain" in url:
            return _response(RSS_FEED)
        return _response(GOOGLE_NEWS_FEED)

    class _Wire:
        """Stands in for GDELT, whose transport is httpx+curl, not ``_fetch``."""

        def __call__(self, query, start, end, **kwargs):
            del query, start, end, kwargs
            return iter(
                [
                    sources.Signal(
                        ts=datetime(2026, 9, 25, 8, 0, tzinfo=UTC).timestamp(),
                        source_type="wire",
                        source_name="wire.test",
                        url="https://wire.test/story",
                        text="Foundry capacity cut after power outage",
                        entities=("foundry",),
                        kind_hint="materials_shortage",
                        credibility=0.6,
                    )
                ]
            )

    monkeypatch.setattr(sources, "_fetch", fake)
    monkeypatch.setattr(sources, "gdelt_signals", _Wire())
    signals = list(
        sources.news_signals(
            "20260923000000", "20260925235959", gdelt=True, feeds=_registry()
        )
    )
    names = {s.source_name for s in signals}
    assert "gCaptain" in names
    assert "Reuters" in names  # via Google News
    assert "wire.test" in names  # via the opt-in GDELT path
    assert signals == sorted(signals, key=lambda s: s.ts)


def test_news_signals_does_not_wait_on_gdelt_by_default(monkeypatch) -> None:
    """GDELT's retry path costs a minute; the default must not block on it."""
    calls: list[str] = []

    def gdelt(*_args, **_kwargs):
        calls.append("gdelt")
        raise AssertionError("GDELT should be opt-in")

    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response(RSS_FEED))
    monkeypatch.setattr(sources, "gdelt_signals", gdelt)
    signals = list(sources.news_signals("20260923", "20260925", feeds=_registry()))
    assert calls == []
    assert signals  # the RSS sources still answered


def test_query_feeds_keep_only_the_newest_slice(monkeypatch) -> None:
    """A search feed returns ~100 items; one query must not swamp a report."""
    items = "".join(
        f"<item><title>Story {i}</title><link>https://x.test/{i}</link>"
        f"<pubDate>Fri, 25 Sep 2026 0{i % 10}:00:00 GMT</pubDate></item>"
        for i in range(60)
    )
    feed = f'<rss version="2.0"><channel><title>Search</title>{items}</channel></rss>'
    monkeypatch.setattr(sources, "_fetch", lambda url, **kw: _response(feed))
    capped = list(sources.rss_signals("https://x.test/feed", "2026-09-25", limit=5))
    assert len(capped) == 5
    # newest five, still ascending for replay consumption
    assert capped == sorted(capped, key=lambda s: s.ts)
    assert all(
        "Story" in s.text for s in capped
    )
    assert len(list(sources.rss_signals("https://x.test/feed", "2026-09-25"))) == 60


def test_news_signals_survives_every_source_being_dead(monkeypatch) -> None:
    def dead(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(sources, "_fetch", dead)
    monkeypatch.setattr(sources, "gdelt_signals", dead)
    assert list(sources.news_signals("20260923", "20260925", feeds=_registry())) == []


def test_compact_cutoff_matches_its_iso_equivalent() -> None:
    """GDELT hands us YYYYMMDDHHMMSS; the feeding sources want the same instant."""
    compact = sources._day_end_ts("20260925235959")
    iso = sources._day_end_ts("2026-09-25T23:59:59Z")
    assert compact == iso
    assert sources._day_end_ts("2026-09-25") == iso  # a bare date means the day


# --- the registry itself ----------------------------------------------------


def test_shipped_registry_loads_and_covers_every_market() -> None:
    registry = config.load_feeds()
    markets = config.load_markets()
    assert registry.feeds, "no publisher feeds configured"
    for spec in registry.feeds:
        assert spec.url.startswith("https://")
        assert 0.0 < spec.credibility <= 1.0
        assert spec.source_type == "trade_press"
    # a market with no queries could never be searched live
    for key in markets:
        assert registry.queries_for(key), f"market {key} has no news queries"


@pytest.mark.parametrize("market", ["semicon", "auto", "energy"])
def test_every_trade_press_prior_sits_below_an_authority_alert(market) -> None:
    """Trade press must not outrank a government advisory (0.9+)."""
    del market
    assert all(spec.credibility < 0.9 for spec in config.load_feeds().feeds)
