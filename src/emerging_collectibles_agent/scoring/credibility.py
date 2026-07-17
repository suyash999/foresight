"""Source credibility scoring and SEO/spam risk detection (deterministic)."""
from __future__ import annotations

import re
from typing import Any

_SPAM_PATTERNS = [
    r"best\s+\d+\s+(products|deals|cards)",
    r"top\s+\d+\s+.*\d{4}",
    r"buy\s+now",
    r"affiliate",
    r"sponsored",
    r"coupon",
    r"discount\s+code",
    r"click\s+here",
]
_SPAM_RE = re.compile("|".join(_SPAM_PATTERNS), re.IGNORECASE)

_LOW_QUALITY_TLDS = {".xyz", ".top", ".click", ".buzz", ".click"}


def base_credibility(source_type: str, baselines: dict[str, float]) -> float:
    return float(baselines.get(source_type, baselines.get("unknown", 0.4)))


def seo_spam_risk(title: str, snippet: str, domain: str) -> float:
    text = f"{title} {snippet}".strip()
    risk = 0.0
    if _SPAM_RE.search(text):
        risk += 0.4
    # excessive listicle numerals
    if re.search(r"\btop\s+\d+\b", text, re.IGNORECASE):
        risk += 0.2
    if any(domain.endswith(t) for t in _LOW_QUALITY_TLDS):
        risk += 0.3
    if len(text) < 12:
        risk += 0.1
    return min(1.0, risk)


def adjust_credibility(base: float, seo_risk: float, freshness: float | None = None,
                       quality_memory: float | None = None) -> float:
    """Blend base credibility with spam risk and learned domain quality."""
    score = base * (1.0 - 0.5 * seo_risk)
    if quality_memory is not None:
        # nudge toward observed quality (learned over time)
        score = 0.7 * score + 0.3 * quality_memory
    if freshness is not None:
        score = 0.85 * score + 0.15 * freshness
    return max(0.0, min(1.0, score))


def evidence_quality_score(signals: dict[str, Any]) -> float:
    """Higher when evidence includes concrete support."""
    weights = {
        "has_product_name": 0.2,
        "has_release_date": 0.15,
        "has_price": 0.15,
        "has_auction_result": 0.12,
        "has_marketplace_rank": 0.1,
        "has_official_announcement": 0.12,
        "has_scarcity_signal": 0.08,
        "has_direct_product_page": 0.08,
    }
    score = sum(w for k, w in weights.items() if signals.get(k))
    # a source quote/snippet is a small bonus
    if signals.get("has_snippet"):
        score += 0.05
    return max(0.0, min(1.0, score))
