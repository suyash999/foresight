"""Optional Playwright renderer for legitimate public JavaScript pages.

Disabled by default. Imported lazily so the system runs without Playwright
installed. Only used when the adaptive router decides JS rendering is needed
and `use_playwright` is enabled. Never bypasses logins/paywalls/CAPTCHAs.
"""
from __future__ import annotations

import time

from ..logging_config import get_logger
from ..models import FetchedPage
from ..util import registered_domain, sha1, utc_iso
from .parsers import parse_page

log = get_logger("scraping.playwright")


class PlaywrightFetcher:
    def __init__(self, config):
        sc = config.get("scraping", {})
        self.enabled = bool(sc.get("use_playwright", False))
        self.user_agent = sc.get("user_agent", "EmergingCollectiblesIntelligenceBot/1.0")
        self.timeout = int(sc.get("request_timeout_seconds", 20)) * 1000
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import playwright  # noqa: F401
            self._available = True
        except Exception:
            self._available = False
        return self._available

    def fetch(self, url: str) -> FetchedPage:
        page = FetchedPage(url=url, domain=registered_domain(url), fetched_at=utc_iso())
        if not self.enabled or not self.available():
            page.status = "error"
            page.error_message = "playwright disabled/unavailable"
            return page
        started = time.time()
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(user_agent=self.user_agent)
                pg = ctx.new_page()
                resp = pg.goto(url, timeout=self.timeout, wait_until="domcontentloaded")
                code = resp.status if resp else None
                if code and code in (401, 403, 429):
                    page.status = "blocked"
                    page.http_status = code
                    page.error_message = f"HTTP {code} (not bypassed)"
                    browser.close()
                    return page
                html = pg.content()
                browser.close()
            parsed = parse_page(html, url)
            page.status = "ok"
            page.html = html
            page.title = parsed["title"]
            page.text = parsed["text"]
            page.published_at = parsed["published_at"] or None
            page.extraction_method = "playwright+" + parsed["extraction_method"].split("+")[-1]
            page.links = parsed["links"]
            page.jsonld = parsed["jsonld"]
            page.text_hash = sha1(page.text[:5000])
        except Exception as exc:
            page.status = "error"
            page.error_message = f"playwright error: {exc}"
        page.runtime_seconds = round(time.time() - started, 3)
        return page
