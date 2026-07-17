"""Deterministic, transparent scoring: EPS, VTM, credibility, trend status."""
from __future__ import annotations

from typing import Any


def classify_trend_status(eps: float, vtm: float, thresholds: dict[str, Any]) -> str:
    """Combine EPS + VTM into a final trend status per configured thresholds."""
    strong = thresholds.get("strong_emerging", {"eps": 75, "vtm": 65})
    needs = thresholds.get("emerging_needs_validation", {"eps": 70, "vtm_below": 65})
    validated = thresholds.get("validated_momentum", {"eps": 60, "vtm": 75})
    fp = thresholds.get("false_positive", {"eps": 70, "vtm_below": 35})
    watch_eps = thresholds.get("watchlist_eps", [40, 69])
    watch_vtm = thresholds.get("watchlist_vtm", [40, 64])
    low = thresholds.get("low_confidence", {"eps_below": 40, "vtm_below": 40})

    # Order matters: strongest / most specific first.
    if eps >= strong.get("eps", 75) and vtm >= strong.get("vtm", 65):
        return "Strong Emerging"
    if eps >= validated.get("eps", 60) and vtm >= validated.get("vtm", 75):
        return "Validated Momentum"
    if eps >= fp.get("eps", 70) and vtm < fp.get("vtm_below", 35):
        return "Potential False Positive"
    if eps >= needs.get("eps", 70) and vtm < needs.get("vtm_below", 65):
        return "Emerging but Needs Validation"
    if (watch_eps[0] <= eps <= watch_eps[1]) or (watch_vtm[0] <= vtm <= watch_vtm[1]):
        return "Watchlist"
    if eps < low.get("eps_below", 40) and vtm < low.get("vtm_below", 40):
        return "Low Confidence"
    return "Watchlist"


def confidence_from_scores(eps: float, vtm: float) -> str:
    avg = (eps + vtm) / 2.0
    if avg >= 70:
        return "high"
    if avg >= 45:
        return "moderate"
    return "low"
