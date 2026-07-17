from emerging_collectibles_agent.agents.product_normalization_agent import ProductNormalizationAgent
from emerging_collectibles_agent.agents.trend_scoring_agent import TrendScoringAgent
from emerging_collectibles_agent.agents.trend_reasoning_agent import TrendReasoningAgent
from emerging_collectibles_agent.agents.credibility_validation_agent import CredibilityValidationAgent
from emerging_collectibles_agent.exports import PRODUCT_COLUMNS, build_product_row
from emerging_collectibles_agent.models import ProductCandidate


def _build_product(config):
    cands = [
        ProductCandidate(product_name="2024 Topps Chrome Wembanyama Rookie Auto",
                         vertical="Trading Cards", category="Basketball cards",
                         brand="Topps", source_url="http://a.com/1", source_domain="a.com",
                         extraction_method="jsonld", extraction_confidence=0.75,
                         evidence_snippet="sold out numbered /99 rookie auto",
                         scarcity_language="sold out, numbered",
                         observed_at_utc="2026-07-17T00:00:00+00:00"),
        ProductCandidate(product_name="Victor Wembanyama Topps Chrome rookie autograph",
                         vertical="Trading Cards", source_url="http://b.com/2",
                         source_domain="b.com", extraction_method="text-scan",
                         extraction_confidence=0.5,
                         observed_at_utc="2026-07-17T01:00:00+00:00"),
    ]
    norm = ProductNormalizationAgent(config)
    products = norm.normalize(cands, {"a.com": "hobby publication", "b.com": "sports news"})
    return products[0]


def test_full_row_has_all_columns(config):
    product = _build_product(config)
    scorer = TrendScoringAgent(config)
    scored = scorer.score(product, events=[], baseline_mentions=0.0)
    scored["_cred_scores"] = [0.82, 0.8]
    reasoner = TrendReasoningAgent(config, llm=None)
    reason = reasoner.reason(product, scored)
    validator = CredibilityValidationAgent(config)
    validation = validator.validate(product, scored, reason)
    row = build_product_row("9839-test", 9839, product, scored, reason, validation)

    for col in PRODUCT_COLUMNS:
        assert col in row, f"missing column {col}"
    assert 0 <= row["EPS"] <= 100
    assert 0 <= row["VTM"] <= 100
    assert row["final_trend_status"]
    assert row["validation_status"] in (
        "unvalidated", "weak", "moderate", "strong",
        "likely false positive", "blocked / insufficient evidence")
    assert row["product_id"]
    assert "ebay" not in row["source_domains"].lower()


def test_event_not_found_marked(config):
    product = _build_product(config)
    scorer = TrendScoringAgent(config)
    scored = scorer.score(product, events=[], baseline_mentions=0.0)
    assert scored["event"]["linked_event_status"] == "not_found"
    assert scored["event"]["event_confidence_score"] == 0.0
