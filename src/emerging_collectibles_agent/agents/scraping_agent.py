"""Scraping and Rendering Agent.

Fetches pages using the adaptive strategy ladder (HTTP+parsers first, browser
rendering only when enabled/needed). Records per-strategy performance and
classifies failures for the recovery ladder. Fully compliant: robots, rate
limits, 429/403 handling, no login/paywall/CAPTCHA bypass.
"""
from __future__ import annotations

from ..config import Config
from ..logging_config import get_logger
from ..models import FetchedPage
from ..scraping.camoufox_fetcher import CamoufoxFetcher
from ..scraping.http_fetcher import HTTPFetcher
from ..scraping.parsers import parse_soup, extract_jsonld
from ..scraping.playwright_fetcher import PlaywrightFetcher
from ..util import registered_domain
from .adaptive_strategy_router import AdaptiveStrategyRouter

log = get_logger("agent.scrape")


def classify_failure(page: FetchedPage) -> str:
    if page.status == "robots_blocked":
        return "blocked_by_robots"
    if page.http_status == 403:
        return "http_403"
    if page.http_status == 404:
        return "http_404"
    if page.http_status == 429:
        return "http_429"
    msg = (page.error_message or "").lower()
    if "timeout" in msg:
        return "timeout"
    if "non-html" in msg:
        return "parser_failed"
    if page.status == "ok" and len((page.text or "")) < 120:
        return "javascript_required"
    return "parser_failed"


class ScrapingAgent:
    def __init__(self, config: Config, router: AdaptiveStrategyRouter, memory=None):
        self.config = config
        self.router = router
        self.memory = memory
        self.http = HTTPFetcher(config)
        self.playwright = PlaywrightFetcher(config)
        self.camoufox = CamoufoxFetcher(config)

    def close(self):
        self.http.close()

    def _apply_strategy(self, strategy: str, url: str) -> FetchedPage:
        if strategy in ("httpx+trafilatura", "readability", "jsonld"):
            page = self.http.fetch(url)
            if strategy == "jsonld" and page.status == "ok" and not page.jsonld:
                # jsonld strategy but none present — still fine, text used
                pass
            return page
        if strategy == "playwright":
            return self.playwright.fetch(url)
        if strategy == "camoufox":
            return self.camoufox.fetch(url)
        return self.http.fetch(url)

    def fetch(self, url: str, source_type: str = "", max_attempts: int = 3) -> FetchedPage:
        domain = registered_domain(url)
        plan = self.router.select(url, domain, source_type)
        order = plan["fallback_strategy_order"][:max_attempts]
        if self.memory is not None:
            self.memory.record_crawl(domain)

        last_page: FetchedPage | None = None
        for strategy in order:
            page = self._apply_strategy(strategy, url)
            page.extraction_method = page.extraction_method or strategy
            success = page.status == "ok" and len(page.text or "") >= 120
            self.router.registry.record(
                domain, strategy, success=success,
                products=0, confidence=0.0, runtime=page.runtime_seconds,
                failure_type="" if success else classify_failure(page))
            if success:
                if self.memory is not None:
                    self.memory.record_result(domain, success=True)
                return page
            last_page = page
            # blocked → don't hammer with more strategies
            if page.status in ("blocked", "robots_blocked"):
                if self.memory is not None:
                    self.memory.record_result(domain, blocked=True)
                break
        if self.memory is not None and last_page and last_page.status not in ("blocked", "robots_blocked"):
            self.memory.record_result(domain, success=False)
        return last_page or FetchedPage(url=url, domain=domain, status="error",
                                        error_message="no strategy produced content")

    def jsonld_only(self, url: str) -> list[dict]:
        page = self.http.fetch(url)
        if page.status == "ok" and page.html:
            return extract_jsonld(parse_soup(page.html))
        return []
