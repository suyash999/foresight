"""Self-learning operational memory.

Implements the decay / residue-reduction / positive-reinforcement mechanics:
  - SourceQualityScore   (EWMA rolling quality per domain)
  - SourceFatigueScore   = exp(-recent_crawl_count / fatigue_threshold)
  - RecoveryBoost        (restored slowly when a source starts producing value)
  - PriorityAdjusted     = base * exp(-failure_penalty * failure_count)
  - AgentResidueScore    = useless_outputs / total_outputs

Nothing here retrains a model — this is practical self-learning through
operational memory persisted in SQLite and consulted on every future run.
"""
from __future__ import annotations

import math
from collections import defaultdict

from ..db import Database
from ..logging_config import get_logger
from ..util import now_utc, utc_iso

log = get_logger("agent.memory")


class SelfLearningMemory:
    def __init__(self, db: Database, config):
        self.db = db
        lr = config.get("learning", {})
        self.failure_penalty = float(lr.get("failure_penalty", 0.35))
        self.fatigue_threshold = float(lr.get("fatigue_threshold", 8.0))
        self.recovery_boost_step = float(lr.get("recovery_boost_step", 0.1))
        self.alpha = float(lr.get("source_quality_alpha", 0.3))
        self.min_quality = float(lr.get("min_domain_quality", 0.1))
        self._cache: dict[str, dict] = {}
        # in-memory residue tally per run (flushed by orchestrator)
        self._residue: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "useless": 0,
                                                                        "success": 0, "failure": 0})

    # -- source quality -----------------------------------------------------
    def _get_row(self, domain: str) -> dict:
        if domain in self._cache:
            return self._cache[domain]
        row = self.db.query_one("SELECT * FROM source_quality_memory WHERE domain=?", (domain,))
        if not row:
            row = {
                "domain": domain, "source_quality_score": 0.5, "source_fatigue_score": 1.0,
                "recovery_boost": 1.0, "success_count": 0, "failure_count": 0,
                "blocked_count": 0, "duplicate_count": 0, "false_positive_count": 0,
                "products_found": 0, "validated_found": 0, "avg_eps": 0.0, "avg_vtm": 0.0,
                "recent_crawl_count": 0, "last_updated_at": utc_iso(),
            }
        self._cache[domain] = row
        return row

    def _save(self, row: dict) -> None:
        row["last_updated_at"] = utc_iso()
        self.db.upsert("source_quality_memory", row, ["domain"])
        self._cache[row["domain"]] = row

    def record_crawl(self, domain: str) -> None:
        if not domain:
            return
        row = self._get_row(domain)
        row["recent_crawl_count"] = int(row.get("recent_crawl_count", 0)) + 1
        row["source_fatigue_score"] = math.exp(-row["recent_crawl_count"] / self.fatigue_threshold)
        self._save(row)

    def record_result(self, domain: str, *, success: bool = False, blocked: bool = False,
                      duplicate: bool = False, false_positive: bool = False,
                      products: int = 0, validated: int = 0,
                      eps: float | None = None, vtm: float | None = None) -> None:
        if not domain:
            return
        row = self._get_row(domain)
        if success:
            row["success_count"] += 1
        if blocked:
            row["blocked_count"] += 1
            row["failure_count"] += 1
        if duplicate:
            row["duplicate_count"] += 1
        if false_positive:
            row["false_positive_count"] += 1
        row["products_found"] += max(0, products)
        row["validated_found"] += max(0, validated)

        # rolling averages
        if eps is not None:
            row["avg_eps"] = (1 - self.alpha) * row["avg_eps"] + self.alpha * eps
        if vtm is not None:
            row["avg_vtm"] = (1 - self.alpha) * row["avg_vtm"] + self.alpha * vtm

        # quality signal in 0..1
        total = row["success_count"] + row["failure_count"] + 1
        fail_rate = row["failure_count"] / total
        block_rate = row["blocked_count"] / total
        fp_rate = row["false_positive_count"] / max(1, row["products_found"] + 1)
        yield_signal = min(1.0, row["products_found"] / 20.0)
        validated_signal = min(1.0, row["validated_found"] / 10.0)
        eps_signal = row["avg_eps"] / 100.0
        instant = max(0.0, 0.35 * yield_signal + 0.25 * validated_signal
                      + 0.2 * eps_signal + 0.2 * (1 - fail_rate)
                      - 0.3 * block_rate - 0.3 * fp_rate)
        row["source_quality_score"] = max(
            self.min_quality,
            (1 - self.alpha) * row["source_quality_score"] + self.alpha * instant,
        )

        # recovery boost: nudge up on value, down on repeated failure
        if success and products > 0:
            row["recovery_boost"] = min(1.5, row["recovery_boost"] + self.recovery_boost_step)
        elif blocked or false_positive:
            row["recovery_boost"] = max(0.4, row["recovery_boost"] - self.recovery_boost_step)
        self._save(row)

    def domain_priority_multiplier(self, domain: str) -> float:
        """quality * fatigue * recovery_boost, clamped to a sane range."""
        row = self._get_row(domain)
        q = float(row.get("source_quality_score", 0.5))
        f = float(row.get("source_fatigue_score", 1.0))
        r = float(row.get("recovery_boost", 1.0))
        return max(0.05, min(2.0, q * f * r))

    def priority_adjusted(self, base: float, failure_count: int) -> float:
        return base * math.exp(-self.failure_penalty * max(0, failure_count))

    def is_blocked_domain(self, domain: str, min_blocks: int = 2) -> bool:
        """A domain we should stop crawling: it has been blocked (407/403/429/
        robots) at least `min_blocks` times and has never fetched successfully.
        Lets the queue skip hopeless hosts so we don't waste time re-hitting them.
        """
        if not domain:
            return False
        row = self._get_row(domain)
        return (int(row.get("blocked_count", 0)) >= min_blocks
                and int(row.get("success_count", 0)) == 0)

    def decay_fatigue(self, factor: float = 0.5) -> None:
        """Called between cycles: recent crawl counts decay so sources recover."""
        rows = self.db.query("SELECT domain, recent_crawl_count FROM source_quality_memory")
        for r in rows:
            new_count = int(r["recent_crawl_count"] * factor)
            fatigue = math.exp(-new_count / self.fatigue_threshold)
            self.db.execute(
                "UPDATE source_quality_memory SET recent_crawl_count=?, source_fatigue_score=? WHERE domain=?",
                (new_count, fatigue, r["domain"]),
            )
        self._cache.clear()

    # -- agent residue ------------------------------------------------------
    def record_output(self, agent_name: str, useless: bool, success: bool = True) -> None:
        r = self._residue[agent_name]
        r["total"] += 1
        if useless:
            r["useless"] += 1
        if success and not useless:
            r["success"] += 1
        else:
            r["failure"] += 1

    def flush_residue(self, run_id: str) -> None:
        for agent, r in self._residue.items():
            total = max(1, r["total"])
            residue = r["useless"] / total
            self.db.insert("agent_residue", {
                "run_id": run_id, "agent_name": agent, "total_outputs": r["total"],
                "useless_outputs": r["useless"], "residue_score": round(residue, 4),
                "success_count": r["success"], "failure_count": r["failure"],
                "avg_processing_time": 0.0, "last_failure": None,
                "recovery_actions_taken": 0, "updated_at": utc_iso(),
            })
        self._residue.clear()

    def residue_score(self, agent_name: str) -> float:
        r = self._residue.get(agent_name)
        if not r or r["total"] == 0:
            return 0.0
        return r["useless"] / r["total"]
