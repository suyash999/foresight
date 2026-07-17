"""Web Page Scoring Agent.

Scores every discovered page before crawling: relevance, credibility,
freshness, product signal, news signal → a combined crawl-priority score.
"""
from __future__ import annotations

from dateutil import parser as dateparser

from ..config import Config
from ..logging_config import get_logger
from ..models import DiscoveredURL, PageScore, SourceVerification
from ..scoring.eps import freshness_score
from ..taxonomy import detect_signals, map_vertical
from ..util import hours_since

log = get_logger("agent.pagescore")


class PageScoringAgent:
    def __init__(self, config: Config):
        self.config = config
        self.verticals = config.verticals
        sp = config.get("scoring_params", {})
        hl = sp.get("freshness_half_life_hours", {})
        self.default_half_life = float(hl.get("news_sensitive", 48))

    def _freshness(self, published_at) -> float:
        if not published_at:
            return 0.35  # unknown recency → mild prior (favours recall)
        try:
            dt = dateparser.parse(str(published_at))
        except Exception:
            return 0.35
        return freshness_score(hours_since(dt), self.default_half_life)

    def score(self, url: DiscoveredURL, verification: SourceVerification) -> PageScore:
        text = f"{url.title} {url.snippet}"
        vertical, vscore, _ = map_vertical(text, self.verticals)
        signals = detect_signals(text)

        relevance = max(url.relevance_score, vscore)
        product_signal = min(1.0, 0.5 * relevance + 0.3 * signals["release_score"]
                             + 0.2 * signals["marketplace_score"])
        news_signal = signals["news_catalyst_score"]
        fresh = self._freshness(url.published_at)
        cred = verification.source_credibility_score

        priority = (
            0.30 * relevance
            + 0.20 * cred
            + 0.20 * product_signal
            + 0.15 * fresh
            + 0.15 * news_signal
        )
        reason = (
            f"relevance={relevance:.2f}, cred={cred:.2f}, product={product_signal:.2f}, "
            f"fresh={fresh:.2f}, news={news_signal:.2f}"
        )
        return PageScore(
            url=url.url,
            page_relevance_score=round(relevance, 3),
            source_credibility_score=round(cred, 3),
            freshness_score=round(fresh, 3),
            product_signal_score=round(product_signal, 3),
            news_signal_score=round(news_signal, 3),
            crawl_priority_score=round(priority, 4),
            scoring_reason=reason,
        )
