import math

from emerging_collectibles_agent.scoring.eps import (compute_eps, event_proximity_score,
                                                     freshness_score,
                                                     mention_velocity_score,
                                                     source_diversity_score)


def test_freshness_decay():
    assert freshness_score(0, 48) == 1.0
    half = freshness_score(48, 48)
    assert abs(half - math.exp(-1)) < 1e-6
    assert freshness_score(1000, 48) < 0.01


def test_mention_velocity_bounded():
    assert mention_velocity_score(10, 0, 1.0) == 1.0
    assert 0 <= mention_velocity_score(1, 100, 1.0) <= 1
    assert mention_velocity_score(0, 5, 1.0) == 0.0


def test_source_diversity():
    assert source_diversity_score(4, 4) == 1.0
    assert source_diversity_score(2, 4) == 0.5
    assert source_diversity_score(10, 4) == 1.0


def test_event_proximity():
    assert event_proximity_score(0, 14) == 1.0
    assert event_proximity_score(None, 14) == 0.0
    assert event_proximity_score(14, 14) == pytest_approx(math.exp(-1))


def test_compute_eps_all_max():
    comps = {k: 1.0 for k in
             ["freshness", "source_credibility", "mention_velocity", "source_diversity",
              "news_catalyst", "event_proximity", "marketplace_signal", "scarcity_signal",
              "release_signal", "ai_confidence"]}
    b = compute_eps(comps)
    assert abs(b.EPS - 100.0) < 1e-6


def test_compute_eps_all_zero():
    b = compute_eps({})
    assert b.EPS == 0.0


def test_compute_eps_partial():
    comps = {"freshness": 1.0, "source_credibility": 1.0}
    b = compute_eps(comps)
    assert 0 < b.EPS < 100


def pytest_approx(v, tol=1e-6):
    class _A:
        def __eq__(self, other):
            return abs(other - v) < tol
    return _A()
