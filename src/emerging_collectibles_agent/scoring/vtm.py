"""Validated Trend Momentum (VTM).

VTM = 100 * [
  0.22*CredibleSourceCoverage + 0.18*IndependentConfirmation
+ 0.14*MentionAcceleration + 0.14*EvidenceQuality + 0.12*EventValidation
+ 0.08*CrossCategoryCatalyst + 0.07*RecencyConsistency
+ 0.05*NoisePenaltyAdjusted ]

Measures whether a trend is real and validated across credible, independent
evidence — not just noisy hype.
"""
from __future__ import annotations

from typing import Any

from ..models import VTMBreakdown


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def credible_source_coverage(credible_source_count: int, required: int = 3) -> float:
    if required <= 0:
        return 0.0
    return _clamp01(credible_source_count / required)


def independent_confirmation(independent_domain_count: int, total_domain_count: int) -> float:
    if total_domain_count <= 0:
        return 0.0
    return _clamp01(independent_domain_count / total_domain_count)


def mention_acceleration(recent_rate: float, baseline_rate: float, smoothing: float = 1.0) -> float:
    denom = max(baseline_rate, smoothing)
    if denom <= 0:
        return 0.0
    return _clamp01((recent_rate - baseline_rate) / denom)


def noise_penalty_adjusted(penalties: dict[str, float]) -> float:
    """Start at 1.0 and subtract configured penalties, floored at 0."""
    val = 1.0
    for p in penalties.values():
        val -= max(0.0, float(p))
    return _clamp01(val)


DEFAULT_WEIGHTS = {
    "credible_source_coverage": 0.22,
    "independent_confirmation": 0.18,
    "mention_acceleration": 0.14,
    "evidence_quality": 0.14,
    "event_validation": 0.12,
    "cross_category_catalyst": 0.08,
    "recency_consistency": 0.07,
    "noise_penalty_adjusted": 0.05,
}


def compute_vtm(components: dict[str, float], weights: dict[str, Any] | None = None) -> VTMBreakdown:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    c = {k: _clamp01(components.get(k, 0.0)) for k in DEFAULT_WEIGHTS}
    # noise penalty defaults to 1.0 when absent (no penalties)
    if "noise_penalty_adjusted" not in components:
        c["noise_penalty_adjusted"] = 1.0
    total = sum(w[k] * c[k] for k in DEFAULT_WEIGHTS)
    vtm = round(100.0 * total, 2)
    return VTMBreakdown(
        credible_source_coverage=c["credible_source_coverage"],
        independent_confirmation=c["independent_confirmation"],
        mention_acceleration=c["mention_acceleration"],
        evidence_quality=c["evidence_quality"],
        event_validation_score=c["event_validation"],
        cross_category_catalyst=c["cross_category_catalyst"],
        recency_consistency=c["recency_consistency"],
        noise_penalty_adjusted=c["noise_penalty_adjusted"],
        VTM=vtm,
    )


def vtm_interpretation(vtm: float) -> str:
    if vtm >= 80:
        return "Strong validated momentum"
    if vtm >= 60:
        return "Validated but still developing"
    if vtm >= 40:
        return "Early signal, needs more validation"
    if vtm >= 20:
        return "Weak or noisy"
    return "Not validated"
