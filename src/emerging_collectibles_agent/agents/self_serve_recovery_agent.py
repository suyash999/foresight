"""SelfServeRecoveryAgent.

When a source fails, this agent tries the compliant recovery ladder BEFORE any
decay/suppression:
  retry/backoff → alternate parser → article/metadata/JSON-LD → RSS/sitemap →
  browser render (if enabled) → alternative public sources → tool discovery →
  sandbox test → promote/rollback → (only then) decay.

Compliance is absolute: never bypass logins/paywalls/CAPTCHAs or explicit
anti-bot blocks. If a site clearly blocks automation, we find OTHER public
sources discussing the same product/event.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional

from ..config import Config
from ..db import Database
from ..logging_config import get_logger
from ..util import registered_domain, utc_iso
from .adaptive_strategy_router import AdaptiveStrategyRouter
from .tool_discovery_agent import OpenSourceToolDiscoveryAgent

log = get_logger("agent.recovery")

PLUGIN_ROOT = ".plugins"


class SelfServeRecoveryAgent:
    def __init__(self, config: Config, db: Database, scraping_agent,
                 router: AdaptiveStrategyRouter, discovery_agent=None, memory=None):
        self.config = config
        self.db = db
        self.scraper = scraping_agent
        self.router = router
        self.discovery = discovery_agent
        self.memory = memory
        conf = config.get("self_serve_recovery", {})
        self.enabled = bool(conf.get("enabled", True))
        self.max_attempts = int(conf.get("max_recovery_attempts_per_url", 5))
        self.alt_source_enabled = bool(conf.get("alternative_source_search_enabled", True))
        self.auto_install = bool(conf.get("auto_install_tools", False))
        self.install_mode = conf.get("tool_install_mode", "recommend_only")
        self.allow_pypi = bool(conf.get("allow_pypi_install", True))
        self.allow_github = bool(conf.get("allow_github_install", False))
        self.allowlist = set(conf.get("tool_allowlist", []))
        self.require_allowlist = bool(conf.get("require_allowlist_for_install", True))
        self.sandbox_required = bool(conf.get("sandbox_test_required", True))
        self.tool_agent = OpenSourceToolDiscoveryAgent(config, db)

    # -- main recovery entry ------------------------------------------------
    def recover(self, run_id: str, url: str, failure_type: str, source_type: str = "",
                original_strategy: str = "") -> dict:
        if not self.enabled:
            return {"result": "disabled", "products_extracted": 0}
        domain = registered_domain(url)
        outcome = {"result": "unrecovered", "products_extracted": 0,
                   "attempted_strategy": "", "attempted_tool": "",
                   "alternative_sources": [], "notes": ""}

        # 1-9: alternate compliant fetch strategies (router already ladders,
        # but force a fresh attempt with the full ladder here).
        if failure_type in ("parser_failed", "javascript_required", "timeout"):
            page = self.scraper.fetch(url, source_type, max_attempts=self.max_attempts)
            if page.status == "ok" and len(page.text) >= 120:
                outcome.update(result="recovered_by_strategy",
                               attempted_strategy=page.extraction_method,
                               notes="Alternate strategy produced content.")
                self._record(run_id, url, domain, failure_type, original_strategy,
                             page.extraction_method, "", "recovered", 0,
                             page.runtime_seconds, outcome["notes"])
                return outcome

        # 10-13: alternative public sources when blocked
        if failure_type in ("http_403", "blocked_by_robots", "http_429") and self.alt_source_enabled:
            alts = self._find_alternative_sources(url, source_type)
            outcome["alternative_sources"] = alts
            outcome["notes"] = f"Site blocks automation; found {len(alts)} alternative public source(s)."
            outcome["result"] = "alternative_sources_found" if alts else "no_alternative"
            self.db.execute(
                "UPDATE domain_learning_memory SET blocked_flag=1, alternative_source_needed=1, "
                "last_updated_at=? WHERE domain=?", (utc_iso(), domain))
            self._ensure_domain_memory(domain, blocked=True)
            self._record(run_id, url, domain, failure_type, original_strategy,
                         "alternative_source", "", outcome["result"], 0, 0.0, outcome["notes"])
            return outcome

        # 14-17: tool discovery + optional sandbox install
        candidates = self.tool_agent.evaluate_for_failure_class(failure_type)
        recommended = [c for c in candidates if c["evaluation_status"] == "recommended"]
        if recommended:
            top = recommended[0]
            outcome["attempted_tool"] = top["tool_name"]
            outcome["notes"] = (f"Recommended tool '{top['tool_name']}' "
                                f"(score {top['tool_candidate_score']}).")
            if self._may_install(top["tool_name"]):
                installed = self._sandbox_install(top["tool_name"])
                outcome["result"] = "tool_sandboxed" if installed else "tool_recommended"
            else:
                outcome["result"] = "tool_recommended"
        self._record(run_id, url, domain, failure_type, original_strategy,
                     outcome.get("attempted_strategy", ""), outcome["attempted_tool"],
                     outcome["result"], outcome["products_extracted"], 0.0, outcome["notes"])
        return outcome

    # -- alternative public sources -----------------------------------------
    def _find_alternative_sources(self, url: str, source_type: str) -> list[str]:
        if not self.discovery:
            return []
        # derive a query from the blocked URL's slug and run compliant discovery
        slug = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")
        if len(slug) < 4:
            return []
        try:
            found = self.discovery._from_gdelt([("Collectibles", slug)])
            found += self.discovery._from_wikipedia([("Collectibles", slug)])
        except Exception:
            return []
        blocked_domain = registered_domain(url)
        alts = [f.url for f in found
                if f.selection_status == "selected" and registered_domain(f.url) != blocked_domain]
        return list(dict.fromkeys(alts))[:10]

    # -- safe sandbox install -----------------------------------------------
    def _may_install(self, tool_name: str) -> bool:
        if not self.auto_install or self.install_mode == "recommend_only":
            return False
        if not self.allow_pypi:
            return False
        if self.require_allowlist and tool_name not in self.allowlist:
            log.info("Install blocked: %s not on allowlist", tool_name)
            return False
        return True

    def _sandbox_install(self, tool_name: str) -> bool:
        """Install into an ISOLATED venv under .plugins/venvs/. Never touches
        the main runtime. Returns True if sandbox install+import test passed."""
        venv_dir = os.path.join(PLUGIN_ROOT, "venvs", tool_name)
        manifest_dir = os.path.join(PLUGIN_ROOT, "manifests")
        os.makedirs(manifest_dir, exist_ok=True)
        try:
            if not os.path.isdir(venv_dir):
                subprocess.run([sys.executable, "-m", "venv", venv_dir],
                               check=True, capture_output=True, timeout=120)
            pip = os.path.join(venv_dir, "bin", "pip")
            if not os.path.exists(pip):
                pip = os.path.join(venv_dir, "Scripts", "pip.exe")
            subprocess.run([pip, "install", "--quiet", tool_name],
                           check=True, capture_output=True, timeout=300)
            py = os.path.join(venv_dir, "bin", "python")
            if not os.path.exists(py):
                py = os.path.join(venv_dir, "Scripts", "python.exe")
            test = subprocess.run([py, "-c", f"import {tool_name.replace('-', '_')}"],
                                  capture_output=True, timeout=60)
            passed = test.returncode == 0
        except Exception as exc:
            log.warning("Sandbox install failed for %s: %s", tool_name, exc)
            passed = False

        import json
        manifest = {
            "tool_name": tool_name, "source_type": "pip",
            "source_url": f"https://pypi.org/project/{tool_name}/",
            "version_or_commit_sha": "latest", "license": "see-pypi",
            "install_time": utc_iso(), "installed_by_agent": "SelfServeRecoveryAgent",
            "reason_for_install": "recovery ladder", "failure_class_targeted": "",
            "sandbox_test_status": "passed" if passed else "failed",
            "promoted_to_active": False, "rollback_available": True,
            "security_notes": "sandboxed venv; not imported into main runtime",
        }
        with open(os.path.join(manifest_dir, f"{tool_name}.json"), "w") as fh:
            json.dump(manifest, fh, indent=2)
        self.db.insert("installed_tools", {
            "tool_name": tool_name, "version_or_commit_sha": "latest",
            "source_url": manifest["source_url"], "install_path": venv_dir,
            "installed_at": utc_iso(), "install_mode": self.install_mode,
            "sandbox_test_status": manifest["sandbox_test_status"],
            "active_status": "sandbox_only", "rollback_status": "available",
            "notes": "not promoted to main runtime",
        })
        return passed

    # -- helpers ------------------------------------------------------------
    def _ensure_domain_memory(self, domain: str, blocked: bool = False):
        self.db.upsert("domain_learning_memory", {
            "domain": domain, "best_strategy": "", "second_best_strategy": "",
            "blocked_flag": 1 if blocked else 0, "alternative_source_needed": 1 if blocked else 0,
            "source_quality_score": 0.3 if blocked else 0.5, "extraction_success_rate": 0.0,
            "validation_success_rate": 0.0, "false_positive_rate": 0.0,
            "last_updated_at": utc_iso(),
        }, ["domain"])

    def _record(self, run_id, url, domain, failure_type, original_strategy,
                attempted_strategy, attempted_tool, result, products, runtime, notes):
        self.db.insert("recovery_attempts", {
            "failure_id": None, "run_id": run_id, "url": url, "domain": domain,
            "failure_type": failure_type, "original_strategy": original_strategy,
            "attempted_strategy": attempted_strategy, "attempted_tool": attempted_tool,
            "result": result, "products_extracted": products,
            "extraction_confidence": 0.0, "runtime_seconds": runtime,
            "notes": notes[:400], "timestamp": utc_iso(),
        })
