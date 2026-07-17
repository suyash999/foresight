"""Optional Camoufox renderer (last resort for legitimate public JS pages).

Disabled by default. Install with `pip install camoufox[geoip]` then fetch the
browser with `python3 -m camoufox fetch`. Imported lazily. Never bypasses
logins/paywalls/CAPTCHAs or explicit anti-bot blocks — if a site clearly
blocks automation, we mark it blocked and move on.
"""
from __future__ import annotations

import time

from ..logging_config import get_logger
from ..models import FetchedPage
from ..util import registered_domain, sha1, utc_iso
from .parsers import parse_page

log = get_logger("scraping.camoufox")


class CamoufoxFetcher:
    def __init__(self, config):
        sc = config.get("scraping", {})
        self.enabled = bool(sc.get("use_camoufox", False))
        self.timeout = int(sc.get("request_timeout_seconds", 20)) * 1000
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import camoufox  # noqa: F401
            self._available = True
        except Exception:
            self._available = False
        return self._available

    def fetch(self, url: str) -> FetchedPage:
        page = FetchedPage(url=url, domain=registered_domain(url), fetched_at=utc_iso())
        if not self.enabled or not self.available():
            page.status = "error"
            page.error_message = "camoufox disabled/unavailable"
            return page
        started = time.time()
        try:
            from camoufox.sync_api import Camoufox

            with Camoufox(headless=True) as browser:
                pg = browser.new_page()
                resp = pg.goto(url, timeout=self.timeout, wait_until="domcontentloaded")
                code = getattr(resp, "status", None)
                if code and code in (401, 403, 429):
                    page.status = "blocked"
                    page.http_status = code
                    page.error_message = f"HTTP {code} (not bypassed)"
                    return page
                html = pg.content()
            parsed = parse_page(html, url)
            page.status = "ok"
            page.html = html
            page.title = parsed["title"]
            page.text = parsed["text"]
            page.published_at = parsed["published_at"] or None
            page.extraction_method = "camoufox+" + parsed["extraction_method"].split("+")[-1]
            page.links = parsed["links"]
            page.jsonld = parsed["jsonld"]
            page.text_hash = sha1(page.text[:5000])
        except Exception as exc:
            page.status = "error"
            page.error_message = f"camoufox error: {exc}"
        page.runtime_seconds = round(time.time() - started, 3)
        return page
