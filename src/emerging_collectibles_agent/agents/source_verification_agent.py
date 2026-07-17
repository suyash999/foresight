"""External Source Verification Agent.

Decides whether a discovered source should be trusted, crawled, ignored,
blocked, or deprioritised. Enforces the eBay/external-only exclusion, classifies
source type, detects SEO/spam risk, and produces a credibility score.
"""
from __future__ import annotations

from ..config import Config
from ..logging_config import get_logger
from ..models import DiscoveredURL, SourceVerification
from ..scoring.credibility import base_credibility, seo_spam_risk, adjust_credibility
from ..util import registered_domain

log = get_logger("agent.verify")


class SourceVerificationAgent:
    def __init__(self, config: Config, memory=None):
        self.config = config
        self.memory = memory
        self.excluded = set(config.excluded_domains)
        self.allowed = set(config.allowed_domains)
        self.baselines = config.get("source_credibility_baselines", {})
        self.known_sources = config.get("known_sources", {})

    def _is_excluded(self, domain: str) -> bool:
        d = (domain or "").lower()
        for ex in self.excluded:
            if d == ex or d.endswith("." + ex) or ex in d:
                return True
        if self.allowed:
            return not any(d == a or d.endswith("." + a) for a in self.allowed)
        return False

    def verify(self, url: DiscoveredURL) -> SourceVerification:
        domain = url.domain or registered_domain(url.url)
        sv = SourceVerification(source_url=url.url, domain=domain,
                                source_type=url.source_type or "unknown")

        if self._is_excluded(domain):
            sv.verification_status = "blocked"
            sv.crawl_allowed = False
            sv.exclusion_reason = "excluded domain (eBay / external-only policy)"
            sv.verification_reason = "Domain is on the exclusion list."
            sv.source_credibility_score = 0.0
            return sv

        # source type: known override
        known = self.known_sources.get(domain)
        if known:
            sv.source_type = known.get("source_type", sv.source_type)

        base = base_credibility(sv.source_type, self.baselines)
        if known and "credibility" in known:
            base = max(base, float(known["credibility"]))

        risk = seo_spam_risk(url.title, url.snippet, domain)
        sv.seo_spam_risk = round(risk, 3)

        quality = None
        if self.memory is not None:
            qrow = self.memory._get_row(domain)
            quality = float(qrow.get("source_quality_score", 0.5))

        sv.source_credibility_score = round(adjust_credibility(base, risk, quality_memory=quality), 3)

        # decisions
        if risk >= 0.6:
            sv.verification_status = "deprioritized"
            sv.verification_reason = f"High SEO/spam risk ({risk:.2f})."
            sv.crawl_allowed = True  # still crawlable, just low priority
        elif sv.source_credibility_score < 0.25:
            sv.verification_status = "deprioritized"
            sv.verification_reason = "Low credibility source."
        else:
            sv.verification_status = "trusted"
            sv.verification_reason = f"{sv.source_type} credibility={sv.source_credibility_score}"
        sv.crawl_allowed = url.crawl_allowed and sv.verification_status != "blocked"
        return sv
