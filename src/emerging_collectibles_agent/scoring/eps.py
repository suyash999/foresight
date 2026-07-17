"""Emerging Product Score (EPS).

EPS = 100 * [
  0.17*Freshness + 0.13*SourceCredibility + 0.14*MentionVelocity
+ 0.12*SourceDiversity + 0.12*NewsCatalyst + 0.10*EventProximity
+ 0.08*MarketplaceSignal + 0.06*ScarcitySignal + 0.05*ReleaseSignal
+ 0.03*AIConfidence ]

All components are normalised to 0..1. Weights come from config so they can be
tuned without code changes.
"""
from __future__ import annotations

import math
from typing import Any

from ..models import EPSBreakdown


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def freshness_score(hours_since_latest: float, half_life_hours: float) -> float:
    if half_life_hours <= 0:
        return 0.0
    return _clamp01(math.exp(-hours_since_latest / half_life_hours))


def mention_velocity_score(recent_mentions: float, baseline_mentions: float,
                           smoothing: float = 1.0) -> float:
    denom = baseline_mentions + smoothing
    if denom <= 0:
        return 0.0
    return _clamp01(recent_mentions / denom)


def source_diversity_score(unique_source_types: int, target: int = 4) -> float:
    if target <= 0:
        return 0.0
    return _clamp01(unique_source_types / target)


def event_proximity_score(days_to_or_since_event: float | None,
                          half_life_days: float) -> float:
    if days_to_or_since_event is None or half_life_days <= 0:
        return 0.0
    return _clamp01(math.exp(-abs(days_to_or_since_event) / half_life_days))


DEFAULT_WEIGHTS = {
    "freshness": 0.17,
    "source_credibility": 0.13,
    "mention_velocity": 0.14,
    "source_diversity": 0.12,
    "news_catalyst": 0.12,
    "event_proximity": 0.10,
    "marketplace_signal": 0.08,
    "scarcity_signal": 0.06,
    "release_signal": 0.05,
    "ai_confidence": 0.03,
}


def compute_eps(components: dict[str, float], weights: dict[str, Any] | None = None) -> EPSBreakdown:
    """`components` holds 0..1 normalised values keyed by weight names."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    c = {k: _clamp01(components.get(k, 0.0)) for k in DEFAULT_WEIGHTS}
    total = (
        w["freshness"] * c["freshness"]
        + w["source_credibility"] * c["source_credibility"]
        + w["mention_velocity"] * c["mention_velocity"]
        + w["source_diversity"] * c["source_diversity"]
        + w["news_catalyst"] * c["news_catalyst"]
        + w["event_proximity"] * c["event_proximity"]
        + w["marketplace_signal"] * c["marketplace_signal"]
        + w["scarcity_signal"] * c["scarcity_signal"]
        + w["release_signal"] * c["release_signal"]
        + w["ai_confidence"] * c["ai_confidence"]
    )
    eps = round(100.0 * total, 2)
    return EPSBreakdown(
        freshness_score=c["freshness"],
        source_credibility_score=c["source_credibility"],
        mention_velocity_score=c["mention_velocity"],
        source_diversity_score=c["source_diversity"],
        news_catalyst_score=c["news_catalyst"],
        event_proximity_score=c["event_proximity"],
        marketplace_signal_score=c["marketplace_signal"],
        scarcity_signal_score=c["scarcity_signal"],
        release_signal_score=c["release_signal"],
        ai_confidence_score=c["ai_confidence"],
        EPS=eps,
    )


def eps_interpretation(eps: float) -> str:
    if eps >= 80:
        return "Strong emerging product"
    if eps >= 60:
        return "Moderate emerging product"
    if eps >= 40:
        return "Weak / watchlist signal"
    if eps >= 20:
        return "Low signal"
    return "Not emerging"
