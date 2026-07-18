"""Failure and Self-Healing Agent.

Records failures, chooses controlled recovery actions (never blind retries),
applies decay/backoff, and suppresses repeated failures. Works with the
self-learning memory so poor paths decay and good paths recover.
"""
from __future__ import annotations

from datetime import timedelta

from ..db import Database
from ..logging_config import get_logger
from ..util import now_utc, utc_iso

log = get_logger("agent.failure")

RECOVERABLE = {
    "javascript_required": "escalate_render",
    "parser_failed": "alt_parser",
    "timeout": "retry_backoff",
    "http_429": "backoff_suppress",
    "http_403": "alternative_source",
    "http_407": "alternative_source",
    "blocked_by_robots": "alternative_source",
    "llm_json_failed": "deterministic_fallback",
    "no_product_found": "deprioritize",
    "http_404": "drop",
}


class FailureAgent:
    def __init__(self, config: Database, db: Database = None, memory=None):
        # allow (config, db, memory) ordering
        self.config = config
        self.db = db
        self.memory = memory
        sr = {}
        try:
            sr = config.get("self_serve_recovery", {})
        except Exception:
            sr = {}
        self.max_retries = int(sr.get("max_recovery_attempts_per_url", 5))

    def record(self, run_id: str, agent_name: str, url: str, domain: str,
               failure_type: str, failure_reason: str, retry_count: int = 0) -> int:
        recovery_action = RECOVERABLE.get(failure_type, "log_only")
        decay = 0.0
        suppress_until = None
        next_retry = None
        now = now_utc()

        if failure_type in ("http_429",):
            suppress_until = utc_iso(now + timedelta(minutes=30))
            decay = 0.3
        elif failure_type in ("http_403", "http_407", "blocked_by_robots"):
            suppress_until = utc_iso(now + timedelta(hours=6))
            decay = 0.2
        elif failure_type in ("timeout", "parser_failed"):
            next_retry = utc_iso(now + timedelta(minutes=5))
            decay = 0.1
        elif failure_type == "http_404":
            suppress_until = utc_iso(now + timedelta(days=7))

        final_status = "suppressed" if suppress_until else ("retry_scheduled" if next_retry else "recovering")

        self.db.insert("agent_failures", {
            "run_id": run_id, "agent_name": agent_name, "url": url, "domain": domain,
            "failure_type": failure_type, "failure_reason": failure_reason[:400],
            "retry_count": retry_count, "recovery_action": recovery_action,
            "decay_applied": decay, "next_retry_at": next_retry,
            "suppress_until": suppress_until, "final_status": final_status,
            "created_at": utc_iso(now),
        })
        self.db.insert("scraping_failures", {
            "run_id": run_id, "url": url, "domain": domain, "source_type": "",
            "failure_type": failure_type, "failure_reason": failure_reason[:400],
            "failed_strategy": "", "retry_count": retry_count, "timestamp": utc_iso(now),
        })
        if self.memory and decay > 0:
            self.memory.record_result(domain, blocked=failure_type in ("http_429", "http_403", "http_407", "blocked_by_robots"),
                                      success=False)
        log.info("FAILURE %s %s -> %s (%s)", failure_type, url, recovery_action, final_status)
        return recovery_action if isinstance(recovery_action, int) else 0

    def is_suppressed(self, url: str, domain: str) -> bool:
        row = self.db.query_one(
            "SELECT suppress_until FROM agent_failures WHERE (url=? OR domain=?) "
            "AND suppress_until IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (url, domain))
        if not row or not row["suppress_until"]:
            return False
        try:
            from dateutil import parser as dp
            return dp.parse(row["suppress_until"]) > now_utc()
        except Exception:
            return False

    def recovery_action_for(self, failure_type: str) -> str:
        return RECOVERABLE.get(failure_type, "log_only")
