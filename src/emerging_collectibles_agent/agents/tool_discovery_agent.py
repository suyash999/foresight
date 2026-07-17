"""OpenSourceToolDiscoveryAgent.

Finds and scores open-source Python packages / GitHub repos that could improve
crawling, extraction, dedup, etc. Scores candidates BEFORE any use. Never
installs arbitrary code into the main runtime: installs (when explicitly
enabled) happen only in an isolated sandbox venv under `.plugins/`.

Default behaviour is `recommend_only` — nothing is installed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional

import httpx

from ..config import Config
from ..db import Database
from ..logging_config import get_logger
from ..util import utc_iso

log = get_logger("agent.tooldiscovery")

WEIGHTS = {
    "maintenance_score": 0.20,
    "security_score": 0.20,
    "compatibility_score": 0.15,
    "license_score": 0.15,
    "documentation_score": 0.10,
    "popularity_score": 0.10,
    "test_coverage_score": 0.05,
    "usefulness_score": 0.05,
}

# curated seeds mapping failure classes to candidate tools (evaluated, not blindly used)
FAILURE_CLASS_TOOLS = {
    "parser_failed": ["extruct", "goose3", "newspaper3k", "selectolax"],
    "javascript_required": ["requests-html", "playwright"],
    "no_product_found": ["extruct", "microdata"],
    "duplicate_content": ["datasketch", "simhash"],
    "entity": ["spacy", "flashtext"],
}

_OK_LICENSES = {"mit", "bsd", "apache", "apache-2.0", "apache software license",
                "bsd license", "mpl", "isc", "psf"}


class OpenSourceToolDiscoveryAgent:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        conf = config.get("tool_discovery", {})
        self.enabled = bool(conf.get("enabled", True))
        self.search_pypi = bool(conf.get("search_pypi", True))
        self.min_score = float(conf.get("min_candidate_score", 0.75))
        self.require_license = bool(conf.get("require_license", True))
        self._client = httpx.Client(timeout=10, follow_redirects=True)

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass

    def _pypi_meta(self, name: str) -> Optional[dict]:
        if not self.search_pypi:
            return None
        try:
            resp = self._client.get(f"https://pypi.org/pypi/{name}/json")
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    def _score_pypi(self, name: str, meta: dict) -> dict:
        info = meta.get("info", {})
        releases = meta.get("releases", {})
        lic = (info.get("license") or "").lower()
        classifiers = " ".join(info.get("classifiers", [])).lower()
        py3 = "python :: 3" in classifiers or ">=3" in (info.get("requires_python") or "")
        has_docs = bool(info.get("project_urls")) or bool(info.get("description"))
        license_ok = any(k in lic or k in classifiers for k in _OK_LICENSES)

        maintenance = min(1.0, len(releases) / 20.0) if releases else 0.3
        security = 0.7 if license_ok else 0.4
        compatibility = 0.9 if py3 else 0.4
        license_score = 1.0 if license_ok else (0.0 if self.require_license else 0.4)
        documentation = 0.8 if has_docs else 0.3
        popularity = 0.6  # PyPI has no download count in this endpoint; neutral prior
        test_coverage = 0.5
        usefulness = 0.8

        comp = {
            "maintenance_score": maintenance, "security_score": security,
            "compatibility_score": compatibility, "license_score": license_score,
            "documentation_score": documentation, "popularity_score": popularity,
            "test_coverage_score": test_coverage, "usefulness_score": usefulness,
        }
        total = sum(WEIGHTS[k] * comp[k] for k in WEIGHTS)
        comp["tool_candidate_score"] = round(total, 3)
        comp["license"] = lic
        comp["version"] = info.get("version", "")
        comp["summary"] = info.get("summary", "")
        return comp

    def evaluate_for_failure_class(self, failure_class: str) -> list[dict]:
        if not self.enabled:
            return []
        out = []
        for name in FAILURE_CLASS_TOOLS.get(failure_class, []):
            meta = self._pypi_meta(name)
            if not meta:
                # record as evaluated-but-unreachable candidate
                out.append(self._record_candidate(name, {}, failure_class, reachable=False))
                continue
            scored = self._score_pypi(name, meta)
            out.append(self._record_candidate(name, scored, failure_class, reachable=True))
        return out

    def _record_candidate(self, name: str, scored: dict, failure_class: str,
                          reachable: bool) -> dict:
        score = scored.get("tool_candidate_score", 0.0)
        recommend = reachable and score >= self.min_score
        status = "recommended" if recommend else ("below_threshold" if reachable else "unreachable")
        reason = (
            f"score={score} (maint {scored.get('maintenance_score', 0):.2f}, "
            f"sec {scored.get('security_score', 0):.2f}, license {scored.get('license', 'n/a')}) "
            f"for failure class '{failure_class}'"
        ) if reachable else "PyPI metadata unreachable in this environment"
        row = {
            "tool_name": name,
            "source_url": f"https://pypi.org/project/{name}/",
            "source_type": "pip",
            "tool_candidate_score": score,
            "maintenance_score": scored.get("maintenance_score", 0.0),
            "security_score": scored.get("security_score", 0.0),
            "compatibility_score": scored.get("compatibility_score", 0.0),
            "license_score": scored.get("license_score", 0.0),
            "documentation_score": scored.get("documentation_score", 0.0),
            "popularity_score": scored.get("popularity_score", 0.0),
            "test_coverage_score": scored.get("test_coverage_score", 0.0),
            "usefulness_score": scored.get("usefulness_score", 0.0),
            "evaluation_status": status,
            "recommendation_reason": reason,
            "install_status": "not_installed",
            "test_status": "untested",
            "promoted_flag": 0,
            "failure_class_targeted": failure_class,
            "created_at": utc_iso(),
        }
        self.db.insert("tool_candidates", row)
        log.info("TOOL CANDIDATE %s -> %s (%s)", name, status, score)
        return row
