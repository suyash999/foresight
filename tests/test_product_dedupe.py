from emerging_collectibles_agent.agents.product_normalization_agent import ProductNormalizationAgent
from emerging_collectibles_agent.models import ProductCandidate


def _cand(name, url, domain):
    return ProductCandidate(product_name=name, vertical="Trading Cards", source_url=url,
                            source_domain=domain, extraction_method="text-scan",
                            extraction_confidence=0.5, observed_at_utc="2026-07-17T00:00:00+00:00")


def test_dedupe_same_product_diff_wording(config):
    agent = ProductNormalizationAgent(config)
    cands = [
        _cand("2024 Topps Chrome Victor Wembanyama Rookie Auto", "http://a.com/1", "a.com"),
        _cand("Victor Wembanyama 2024 Topps Chrome rookie autograph", "http://b.com/2", "b.com"),
    ]
    products = agent.normalize(cands, {"a.com": "hobby publication", "b.com": "sports news"})
    assert len(products) == 1
    p = products[0]
    assert p.mention_count == 2
    assert p.source_count == 2


def test_distinct_products_not_merged(config):
    agent = ProductNormalizationAgent(config)
    cands = [
        _cand("2024 Topps Chrome Victor Wembanyama Rookie Auto", "http://a.com/1", "a.com"),
        _cand("2023 Panini Prizm LeBron James Base Card", "http://b.com/2", "b.com"),
    ]
    products = agent.normalize(cands, {})
    assert len(products) == 2


def test_product_id_stable(config):
    agent = ProductNormalizationAgent(config)
    p1 = agent.normalize([_cand("Charizard 151 Booster Box", "http://a.com", "a.com")], {})
    p2 = agent.normalize([_cand("Charizard 151 Booster Box", "http://b.com", "b.com")], {})
    assert p1[0].product_id == p2[0].product_id
