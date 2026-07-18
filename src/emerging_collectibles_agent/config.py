"""Configuration loading and access.

Loads `config.yaml`, applies environment overrides via `.env`, and exposes a
thin, dotted-access wrapper. Everything the system does (verticals, sources,
weights, thresholds, budgets, colors) is driven from here so behaviour can be
changed without touching code.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:  # optional; .env is convenience only
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_a, **_k):  # type: ignore
        return False


class Config:
    """Dictionary-backed config with dotted lookups and sensible defaults."""

    def __init__(self, data: dict[str, Any], path: str | None = None):
        self._data = data
        self.path = path

    # -- access helpers ------------------------------------------------------
    def get(self, dotted_key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted_key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    def reload(self) -> bool:
        """Re-read config.yaml + runtime_overrides.yaml in place. Returns True if
        reloaded. Flag-style settings read via get() at cycle time go live; values
        cached in agent __init__ (source lists) need a worker restart."""
        if not self.path:
            return False
        try:
            self._data = _load_merged(self.path)
            return True
        except Exception:
            return False

    # -- convenience accessors used across the codebase ----------------------
    @property
    def verticals(self) -> list[str]:
        return list(self.get("verticals", []))

    @property
    def excluded_domains(self) -> list[str]:
        return [d.lower() for d in self.get("excluded_domains", [])]

    @property
    def allowed_domains(self) -> list[str]:
        return [d.lower() for d in self.get("allowed_domains", [])]

    @property
    def external_only_mode(self) -> bool:
        return bool(self.get("external_only_mode", True))

    @property
    def run_reference(self) -> int:
        return int(self.get("run_reference", 9839))

    @property
    def database_path(self) -> str:
        return str(self.get("database_path", "data/collectibles.db"))

    @property
    def output_dir(self) -> str:
        return str(self.get("output_dir", "outputs"))

    @property
    def timezone(self) -> str:
        return str(self.get("timezone", "Asia/Kolkata"))

    def env(self, env_var_name: str, default: str | None = None) -> str | None:
        """Resolve a value from the environment (never hardcode secrets)."""
        if not env_var_name:
            return default
        return os.environ.get(env_var_name, default)


def _default_config_path() -> str:
    # Prefer repo-root config.yaml relative to CWD, else package-relative.
    cwd_cfg = Path.cwd() / "config.yaml"
    if cwd_cfg.exists():
        return str(cwd_cfg)
    pkg_cfg = Path(__file__).resolve().parents[2] / "config.yaml"
    return str(pkg_cfg)


def _deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge `over` into `base` (returns base, modified in place)."""
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def overrides_path_for(cfg_path: str) -> str:
    """Sibling runtime_overrides.yaml — dashboard toggles write here so the
    commented config.yaml is never rewritten."""
    return os.path.join(os.path.dirname(os.path.abspath(cfg_path)), "runtime_overrides.yaml")


def _load_merged(cfg_path: str) -> dict:
    with open(cfg_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config.yaml must be a mapping, got {type(data)}")
    ov_path = overrides_path_for(cfg_path)
    if os.path.exists(ov_path):
        try:
            with open(ov_path, "r", encoding="utf-8") as fh:
                over = yaml.safe_load(fh) or {}
            if isinstance(over, dict):
                _deep_merge(data, over)
        except Exception:
            pass
    return data


def load_config(path: str | None = None) -> Config:
    """Load configuration from YAML (+ optional runtime_overrides.yaml), applying `.env` first."""
    load_dotenv(override=False)
    cfg_path = path or _default_config_path()
    return Config(_load_merged(cfg_path), path=cfg_path)
