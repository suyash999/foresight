"""Optional Jina Reader fetch strategy (agent-reach style, compliant).

Jina Reader (https://r.jina.ai/<url>) is a public service that fetches a PUBLIC
page and returns clean markdown — useful as a fallback for JS-heavy or
hard-to-parse public pages. It is NOT an anti-bot bypass: it reads public
content only. We still gate every request behind our own robots.txt check on the
TARGET url, and never use it on login/paywalled pages.

Disabled by default; needs a (free-tier) JINA_API_KEY. If unavailable, callers
fall back to the normal strategy ladder.
"""
from __future__ import annotations

import time

import httpx

from ..logging_config import get_logger
from ..models import FetchedPage
from ..util import clean_text, registered_domain, sha1, utc_iso
from .robots import RobotsCache

log = get_logger("scraping.jina")


class JinaReaderFetcher:
    def __init__(self, config):
        sc = config.get("scraping", {})
        self.enabled = bool(sc.get("use_jina_reader", False))
        self.api_key = config.env(sc.get("jina_api_key_env", "JINA_API_KEY"))
        self.timeout = float(sc.get("request_timeout_seconds", 20))
        self.user_agent = sc.get("user_agent", "EmergingCollectiblesIntelligenceBot/1.0")
        self.robots = RobotsCache(self.user_agent, enabled=bool(sc.get("respect_robots_txt", True)))
        self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)

    def available(self) -> bool:
        # requires the feature flag AND a key (Jina now gates access)
        return self.enabled and bool(self.api_key)

    def fetch(self, url: str) -> FetchedPage:
        page = FetchedPage(url=url, domain=registered_domain(url), fetched_at=utc_iso())
        if not self.available():
            page.status = "error"
            page.error_message = "jina reader disabled/no key"
            return page
        # compliance: respect robots on the TARGET, not the reader
        if not self.robots.allowed(url):
            page.status = "robots_blocked"
            page.error_message = "Disallowed by robots.txt (target)"
            return page
        started = time.time()
        try:
            headers = {"Authorization": f"Bearer {self.api_key}",
                       "X-Return-Format": "markdown", "Accept": "text/plain"}
            resp = self._client.get(f"https://r.jina.ai/{url}", headers=headers)
            if resp.status_code in (401, 402, 403, 429):
                page.status = "blocked"
                page.http_status = resp.status_code
                page.error_message = f"jina HTTP {resp.status_code}"
                return page
            if resp.status_code != 200:
                page.status = "error"
                page.error_message = f"jina HTTP {resp.status_code}"
                return page
            text = resp.text or ""
            # first markdown heading (if any) as a title hint
            title = ""
            for line in text.splitlines():
                if line.strip().startswith("#"):
                    title = line.strip("# ").strip()
                    break
            page.status = "ok"
            page.text = clean_text(text, 20000)
            page.title = title[:300]
            page.extraction_method = "jina_reader"
            page.text_hash = sha1(page.text[:5000])
        except Exception as exc:
            page.status = "error"
            page.error_message = f"jina error: {exc}"
        page.runtime_seconds = round(time.time() - started, 3)
        return page

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
