from emerging_collectibles_agent.scoring import classify_trend_status
from emerging_collectibles_agent.scoring.vtm import (compute_vtm, credible_source_coverage,
                                                     independent_confirmation,
                                                     mention_acceleration,
                                                     noise_penalty_adjusted)


def test_credible_source_coverage():
    assert credible_source_coverage(3, 3) == 1.0
    assert credible_source_coverage(1, 3) < 1.0
    assert credible_source_coverage(5, 3) == 1.0


def test_independent_confirmation():
    assert independent_confirmation(4, 4) == 1.0
    assert independent_confirmation(2, 4) == 0.5
    assert independent_confirmation(0, 0) == 0.0


def test_mention_acceleration_clamped():
    assert mention_acceleration(10, 1, 1.0) == 1.0
    assert mention_acceleration(1, 10, 1.0) == 0.0


def test_noise_penalty():
    assert noise_penalty_adjusted({}) == 1.0
    assert noise_penalty_adjusted({"a": 0.25, "b": 0.25}) == 0.5
    assert noise_penalty_adjusted({"a": 2.0}) == 0.0


def test_compute_vtm_all_max():
    comps = {k: 1.0 for k in
             ["credible_source_coverage", "independent_confirmation", "mention_acceleration",
              "evidence_quality", "event_validation", "cross_category_catalyst",
              "recency_consistency", "noise_penalty_adjusted"]}
    b = compute_vtm(comps)
    assert abs(b.VTM - 100.0) < 1e-6


def test_trend_status_classification(config):
    th = config.get("validation_thresholds", {})
    assert classify_trend_status(82, 71, th) == "Strong Emerging"
    assert classify_trend_status(72, 40, th) == "Emerging but Needs Validation"
    assert classify_trend_status(62, 78, th) == "Validated Momentum"
    assert classify_trend_status(75, 20, th) == "Potential False Positive"
    assert classify_trend_status(50, 50, th) == "Watchlist"
    assert classify_trend_status(10, 10, th) == "Low Confidence"
