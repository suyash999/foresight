"""Credibility Validation Agent.

Assigns a validation status to each product by weighing independent credible
source coverage, catalyst presence, concrete evidence, and counter-signals.

Validation statuses:
  unvalidated | weak | moderate | strong | likely false positive |
  blocked / insufficient evidence
"""
from __future__ import annotations

from ..config import Config
from ..logging_config import get_logger
from .product_normalization_agent import NormalizedProduct

log = get_logger("agent.validate")


class CredibilityValidationAgent:
    def __init__(self, config: Config):
        self.config = config
        sp = config.get("scoring_params", {})
        self.required_credible = int(sp.get("required_credible_sources", 3))

    def validate(self, product: NormalizedProduct, scored: dict, reason) -> str:
        eps = scored["EPS"]
        vtm = scored["VTM"]
        credible = scored["credible_source_count"]
        independent = scored["independent_domain_count"]
        status_trend = scored["final_trend_status"]
        agg = scored["aggregate_signals"]
        has_concrete = (agg["has_price"] or agg["has_release"] or agg["has_auction"]
                        or agg["marketplace"] > 0.3)

        if product.source_count == 0:
            return "blocked / insufficient evidence"
        if status_trend == "Potential False Positive" or (eps >= 70 and vtm < 35):
            return "likely false positive"
        if credible >= self.required_credible and independent >= 3 and vtm >= 65:
            return "strong"
        if credible >= 2 and independent >= 2 and (vtm >= 45 or has_concrete):
            return "moderate"
        if independent >= 2 or has_concrete or eps >= 50:
            return "weak"
        return "unvalidated"

    def validate_source(self, domain: str, source_type: str, credibility: float,
                        seo_risk: float, independent: bool, fresh: float) -> dict:
        return {
            "domain": domain,
            "source_type": source_type,
            "credibility_score": round(credibility, 3),
            "seo_spam_risk": round(seo_risk, 3),
            "independent": independent,
            "freshness": round(fresh, 3),
            "external_to_ebay": "ebay" not in domain.lower(),
            "gives_concrete_evidence": credibility >= 0.6,
        }
