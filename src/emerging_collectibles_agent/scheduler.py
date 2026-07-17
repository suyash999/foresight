"""Background scheduler for continuous operation.

Runs the orchestrator cycle on an interval, emits heartbeats between cycles so
the dashboard can tell whether the worker is live or stale, supports a max
cycle cap, and shuts down gracefully on SIGINT/SIGTERM.
"""
from __future__ import annotations

import signal
import time
from datetime import timedelta

from .config import Config
from .logging_config import get_logger
from .orchestrator import Orchestrator
from .util import utc_iso, now_utc

log = get_logger("scheduler")


class Scheduler:
    def __init__(self, config: Config):
        self.config = config
        rt = config.get("runtime", {})
        self.interval = int(rt.get("crawl_interval_seconds", 900))
        self.heartbeat_seconds = int(rt.get("worker_heartbeat_seconds", 15))
        self.max_cycles = int(rt.get("max_cycles", 0))
        self._stop = False

    def _install_signals(self):
        def handler(signum, _frame):
            log.info("Signal %s received — finishing current cycle then stopping.", signum)
            self._stop = True
        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except Exception:
            pass  # not in main thread

    def run_forever(self):
        self._install_signals()
        orch = Orchestrator(self.config)
        cycles = 0
        log.info("Worker starting: interval=%ss max_cycles=%s", self.interval,
                 self.max_cycles or "unlimited")
        try:
            while not self._stop:
                try:
                    result = orch.run_cycle()
                    log.info("Cycle %d complete: %s", cycles + 1, result.get("run_id"))
                except Exception as exc:
                    log.exception("Cycle failed: %s", exc)
                cycles += 1
                if self.max_cycles and cycles >= self.max_cycles:
                    log.info("Reached max_cycles=%d — stopping.", self.max_cycles)
                    break
                next_run = utc_iso(now_utc() + timedelta(seconds=self.interval))
                self._sleep_with_heartbeat(orch, next_run)
        finally:
            orch.close()
            log.info("Worker stopped after %d cycle(s).", cycles)

    def _sleep_with_heartbeat(self, orch: Orchestrator, next_run_at: str):
        waited = 0
        while waited < self.interval and not self._stop:
            orch.db.insert("worker_heartbeat", {
                "timestamp": utc_iso(), "worker_status": "sleeping",
                "current_loop_state": "SLEEP", "current_run_id": "",
                "urls_discovered_this_cycle": 0, "urls_crawled_this_cycle": 0,
                "products_extracted_this_cycle": 0, "events_extracted_this_cycle": 0,
                "errors_this_cycle": 0, "next_run_at": next_run_at,
            })
            step = min(self.heartbeat_seconds, self.interval - waited)
            time.sleep(step)
            waited += step
