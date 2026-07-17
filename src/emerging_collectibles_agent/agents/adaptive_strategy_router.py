"""AdaptiveStrategyRouter + StrategyRegistry.

Chooses an extraction/fetch strategy order per URL using learned per-domain
performance. Strategy order is NOT static — it evolves from observed success
rates recorded in the `strategy_registry` table.
"""
from __future__ import annotations

from ..db import Database
from ..logging_config import get_logger
from ..util import utc_iso

log = get_logger("agent.router")

# Ordered ladder of compliant strategies (cheapest / most-common first).
DEFAULT_STRATEGY_ORDER = [
    "httpx+trafilatura",
    "jsonld",
    "readability",
    "playwright",
    "camoufox",
]


class StrategyRegistry:
    def __init__(self, db: Database):
        self.db = db

    def record(self, domain: str, strategy: str, *, success: bool, products: int = 0,
               confidence: float = 0.0, runtime: float = 0.0,
               failure_type: str = "") -> None:
        row = self.db.query_one(
            "SELECT * FROM strategy_registry WHERE domain=? AND strategy_name=?",
            (domain, strategy))
        now = utc_iso()
        if not row:
            row = {
                "domain": domain, "source_type": "", "strategy_name": strategy,
                "success_count": 0, "failure_count": 0, "success_rate": 0.0,
                "avg_products_extracted": 0.0, "avg_confidence": 0.0,
                "avg_runtime_seconds": 0.0, "last_success_at": None,
                "last_failure_at": None, "priority_rank": 100, "active_flag": 1,
                "best_for_failure_types": "", "notes": "",
            }
        sc = row["success_count"] + (1 if success else 0)
        fc = row["failure_count"] + (0 if success else 1)
        n = max(1, sc + fc)
        row["success_count"] = sc
        row["failure_count"] = fc
        row["success_rate"] = round(sc / n, 3)
        # incremental averages
        row["avg_products_extracted"] = round(
            (row["avg_products_extracted"] * (n - 1) + products) / n, 3)
        row["avg_confidence"] = round(
            (row["avg_confidence"] * (n - 1) + confidence) / n, 3)
        row["avg_runtime_seconds"] = round(
            (row["avg_runtime_seconds"] * (n - 1) + runtime) / n, 3)
        if success:
            row["last_success_at"] = now
        else:
            row["last_failure_at"] = now
            if failure_type and failure_type not in (row.get("best_for_failure_types") or ""):
                pass
        self.db.upsert("strategy_registry", row, ["domain", "strategy_name"])

    def best_order(self, domain: str) -> list[str]:
        rows = self.db.query(
            "SELECT strategy_name, success_rate, success_count FROM strategy_registry "
            "WHERE domain=? AND active_flag=1 ORDER BY success_rate DESC, success_count DESC",
            (domain,))
        learned = [r["strategy_name"] for r in rows if r["success_count"] > 0]
        # merge learned order in front of defaults, keep unique
        order = []
        for s in learned + DEFAULT_STRATEGY_ORDER:
            if s not in order:
                order.append(s)
        return order


class AdaptiveStrategyRouter:
    def __init__(self, config, db: Database, memory=None):
        self.config = config
        self.db = db
        self.memory = memory
        conf = config.get("adaptive_strategy_router", {})
        self.enabled = bool(conf.get("enabled", True))
        self.use_registry = bool(conf.get("use_strategy_registry", True))
        self.min_success = float(conf.get("min_success_rate_for_priority", 0.60))
        self.max_fail_before_alt = float(conf.get("max_failure_rate_before_alternative_source", 0.70))
        self.use_playwright = bool(config.get("scraping.use_playwright", False))
        self.use_camoufox = bool(config.get("scraping.use_camoufox", False))
        self.registry = StrategyRegistry(db)

    def select(self, url: str, domain: str, source_type: str = "") -> dict:
        order = DEFAULT_STRATEGY_ORDER[:]
        reason = "default ladder"
        if self.enabled and self.use_registry:
            learned = self.registry.best_order(domain)
            if learned:
                order = learned
                reason = "learned from strategy_registry"
        # filter out disabled browser strategies
        filtered = []
        for s in order:
            if s == "playwright" and not self.use_playwright:
                continue
            if s == "camoufox" and not self.use_camoufox:
                continue
            filtered.append(s)
        if not filtered:
            filtered = ["httpx+trafilatura", "jsonld", "readability"]
        return {
            "selected_strategy": filtered[0],
            "fallback_strategy_order": filtered,
            "reason_for_selection": reason,
        }

    def domain_failure_rate(self, domain: str) -> float:
        rows = self.db.query(
            "SELECT SUM(success_count) s, SUM(failure_count) f FROM strategy_registry WHERE domain=?",
            (domain,))
        if not rows or rows[0]["s"] is None:
            return 0.0
        s = rows[0]["s"] or 0
        f = rows[0]["f"] or 0
        n = s + f
        return (f / n) if n else 0.0

    def needs_alternative_source(self, domain: str) -> bool:
        return self.domain_failure_rate(domain) >= self.max_fail_before_alt
