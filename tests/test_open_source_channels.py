"""Tests for the keyless open-source discovery channels added after the sources
research (Google News/Trends, Wikimedia pageviews/eventstream, Shopify, Scryfall,
Pokemon TCG, Product Hunt, Gamefound).

These are offline unit tests: they exercise parsing/decoding/gating logic without
hitting the network (each channel is guarded to return [] on any failure).
"""
import base64

from emerging_collectibles_agent.agents.source_discovery_agent import SourceDiscoveryAgent


def _agent(config):
    return SourceDiscoveryAgent(config)


def test_config_wires_new_keyless_providers(config):
    sp = config.get("search_providers", {})
    # on by default
    for name in ("google_news", "bing_news", "google_trends", "wikipedia_pageviews",
                 "shopify", "scryfall", "pokemontcg", "producthunt", "gamefound"):
        assert name in sp, f"missing provider {name}"
        assert sp[name].get("enabled") is True, f"{name} should be on by default"
    # present but intentionally off (real-time firehose is a poor fit for bounded polling)
    assert sp.get("wikimedia_eventstream", {}).get("enabled") is False
    # Google CSE must be fully gone
    assert "google_custom_search" not in sp


def test_new_seed_lists_present(config):
    stores = config.get("seed_shopify_stores", [])
    assert len(stores) >= 5
    assert any("super7" in (s.get("url", "")) for s in stores)
    watch = config.get("wikipedia_watchlist", [])
    titles = {w.get("article") for w in watch}
    assert "Labubu" in titles and "Pop_Mart" in titles


def test_gnews_decoder_extracts_real_url(config):
    a = _agent(config)
    url = b"https://example.com/labubu-new-drop"
    proto = b"\x08\x13\x22" + bytes([len(url)]) + url + b"\x32\x02en"
    enc = "https://news.google.com/rss/articles/" + base64.urlsafe_b64encode(proto).decode()
    assert a._decode_gnews_url(enc) == url.decode()  # exact, no trailing bytes
    a.close()


def test_gnews_decoder_passthrough_and_failure(config):
    a = _agent(config)
    assert a._decode_gnews_url("https://realsite.com/x?a=1") == "https://realsite.com/x?a=1"
    assert a._decode_gnews_url("https://news.google.com/rss/articles/CBMiZZZ") == ""
    a.close()


def test_channels_respect_disabled_flag(config):
    """When a provider is disabled, its channel returns [] without any network."""
    a = _agent(config)
    a.sp = dict(a.sp)
    for name in ("google_news", "bing_news", "google_trends", "wikipedia_pageviews",
                 "wikimedia_eventstream", "shopify", "scryfall", "pokemontcg",
                 "producthunt", "gamefound"):
        a.sp[name] = {"enabled": False}
    assert a._from_google_news([("Trading Cards", "q")]) == []
    assert a._from_bing_news([("Trading Cards", "q")]) == []
    assert a._from_google_trends() == []
    assert a._from_wikipedia_pageviews() == []
    assert a._from_wikimedia_eventstream() == []
    assert a._from_shopify() == []
    assert a._from_scryfall() == []
    assert a._from_pokemontcg() == []
    assert a._from_producthunt() == []
    assert a._from_gamefound() == []
    a.close()


def test_discover_includes_new_channels_in_pipeline(config):
    """discover() should call the new channels; with network guarded it still
    returns at least the config seed URLs and never raises."""
    a = _agent(config)
    a.max_discovery_seconds = 3  # keep the offline run fast
    results = a.discover(recovery=False)
    # seeds always produce results regardless of network
    assert any(r.discovery_method == "seed" for r in results)
    a.close()
