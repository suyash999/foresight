from emerging_collectibles_agent.agents.source_discovery_agent import SourceDiscoveryAgent
from emerging_collectibles_agent.util import normalize_url


def test_normalize_url_strips_tracking():
    u = normalize_url("https://example.com/page?utm_source=x&id=5&fbclid=abc")
    assert "utm_source" not in u
    assert "fbclid" not in u
    assert "id=5" in u


def test_normalize_url_resolves_relative():
    u = normalize_url("/news/item", base="https://example.com/section/")
    assert u == "https://example.com/news/item"


def test_ebay_domain_rejected(config):
    agent = SourceDiscoveryAgent(config)
    du = agent._make_url("https://www.ebay.com/itm/12345", "seed", "test", "public marketplace")
    assert du is not None
    assert du.selection_status == "rejected"
    assert "excluded" in du.rejection_reason.lower()
    agent.close()


def test_url_dedupe(config):
    agent = SourceDiscoveryAgent(config)
    a = agent._make_url("https://www.cardboardconnection.com/topps-2024", "rss", "feed",
                        "hobby publication", ["Trading Cards"], title="Topps 2024 release")
    b = agent._make_url("https://www.cardboardconnection.com/topps-2024", "rss", "feed",
                        "hobby publication", ["Trading Cards"], title="Topps 2024 release")
    deduped = agent._dedupe([a, b])
    dup_flags = [d.duplicate_flag for d in deduped]
    assert any(dup_flags)
    agent.close()
