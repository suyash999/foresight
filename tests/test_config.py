from emerging_collectibles_agent.config import load_config


def test_config_loads(config):
    assert config.run_reference == 9839
    assert config.external_only_mode is True
    assert len(config.verticals) >= 7
    assert "Trading Cards" in config.verticals


def test_excluded_ebay_domains(config):
    excluded = config.excluded_domains
    assert "ebay.com" in excluded
    assert any("ebay" in d for d in excluded)


def test_eps_weights_sum_to_one(config):
    weights = config.get("eps_weights", {})
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_vtm_weights_sum_to_one(config):
    weights = config.get("vtm_weights", {})
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_dashboard_colors(config):
    colors = config.get("dashboard.colors", {})
    assert colors.get("red") == "#E53238"
    assert colors.get("blue") == "#0064D2"
    assert colors.get("green") == "#86B817"
