"""Responsible HTTP fetching.

Guardrails baked in:
- respects robots.txt (when enabled) and per-domain crawl-delay
- per-domain rate limiting + backoff
- honours HTTP 429 (marks blocked, backs off) and 403/401
- never bypasses logins/paywalls/CAPTCHAs
- caps response size and follows redirects for public pages only
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import httpx

from ..logging_config import get_logger
from ..models import FetchedPage
from ..util import full_host, now_utc, registered_domain, sha1, utc_iso
from .parsers import parse_page
from .robots import RobotsCache

log = get_logger("scraping.http")


class DomainThrottle:
    """Per-domain minimum delay + exponential backoff on failures."""

    def __init__(self, base_delay: float):
        self.base_delay = base_delay
        self._last: dict[str, float] = {}
        self._backoff: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            last = self._last.get(host, 0.0)
            delay = self.base_delay + self._backoff.get(host, 0.0)
        elapsed = time.time() - last
        if elapsed < delay:
            time.sleep(delay - elapsed)
        with self._lock:
            self._last[host] = time.time()

    def penalize(self, host: str, seconds: float = 30.0) -> None:
        with self._lock:
            self._backoff[host] = min(300.0, self._backoff.get(host, 0.0) + seconds)

    def relax(self, host: str) -> None:
        with self._lock:
            if host in self._backoff:
                self._backoff[host] = max(0.0, self._backoff[host] * 0.5)


class HTTPFetcher:
    def __init__(self, config):
        sc = config.get("scraping", {})
        self.user_agent = sc.get("user_agent", "EmergingCollectiblesIntelligenceBot/1.0")
        self.timeout = float(sc.get("request_timeout_seconds", 20))
        self.max_bytes = int(sc.get("max_bytes_per_page", 3_000_000))
        self.retries = int(sc.get("retry_on_network_error", 2))
        self.robots = RobotsCache(self.user_agent, enabled=bool(sc.get("respect_robots_txt", True)))
        self.throttle = DomainThrottle(float(sc.get("per_domain_delay_seconds", 2.0)))
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=self.timeout,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en;q=0.9,*;q=0.5",
            },
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def fetch(self, url: str, extra_headers: Optional[dict] = None) -> FetchedPage:
        host = full_host(url)
        domain = registered_domain(url)
        page = FetchedPage(url=url, domain=domain, fetched_at=utc_iso())
        started = time.time()

        # robots.txt gate
        if not self.robots.allowed(url):
            page.status = "robots_blocked"
            page.error_message = "Disallowed by robots.txt"
            log.info("ROBOTS BLOCK %s", url)
            return page

        crawl_delay = self.robots.crawl_delay(url)
        if crawl_delay:
            time.sleep(min(crawl_delay, 10.0))
        self.throttle.wait(host)

        headers = dict(extra_headers or {})
        last_err = ""
        for attempt in range(self.retries + 1):
            try:
                with self._client.stream("GET", url, headers=headers) as resp:
                    code = resp.status_code
                    page.http_status = code
                    if code == 429:
                        page.status = "blocked"
                        page.error_message = "HTTP 429 rate limited"
                        self.throttle.penalize(host, 60.0)
                        log.info("429 BLOCK %s", url)
                        return page
                    if code in (401, 403):
                        page.status = "blocked"
                        page.error_message = f"HTTP {code} (login/anti-bot; not bypassed)"
                        self.throttle.penalize(host, 30.0)
                        return page
                    if code == 407:
                        # proxy auth required — retrying the same request is
                        # pointless; mark blocked and move on (no retry).
                        page.status = "blocked"
                        page.error_message = "HTTP 407 proxy auth (not retried)"
                        return page
                    if code == 404:
                        page.status = "error"
                        page.error_message = "HTTP 404"
                        return page
                    if code >= 400:
                        page.status = "error"
                        page.error_message = f"HTTP {code}"
                        last_err = page.error_message
                        continue
                    ctype = resp.headers.get("content-type", "")
                    if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                        page.status = "error"
                        page.error_message = f"Non-HTML content-type: {ctype}"
                        return page
                    chunks = bytearray()
                    for chunk in resp.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > self.max_bytes:
                            break
                    html = chunks.decode(resp.encoding or "utf-8", errors="ignore")
                self.throttle.relax(host)
                parsed = parse_page(html, url)
                page.status = "ok"
                page.html = html
                page.title = parsed["title"]
                page.text = parsed["text"]
                page.published_at = parsed["published_at"] or None
                page.extraction_method = parsed["extraction_method"]
                page.links = parsed["links"]
                page.jsonld = parsed["jsonld"]
                page.meta = {
                    "meta_description": parsed["meta_description"],
                    "tables": parsed["tables"][:10],
                }
                page.text_hash = sha1(page.text[:5000])
                page.runtime_seconds = round(time.time() - started, 3)
                return page
            except (httpx.TimeoutException,) as exc:
                last_err = f"timeout: {exc}"
                self.throttle.penalize(host, 10.0)
            except httpx.HTTPError as exc:
                last_err = f"http_error: {exc}"
            except Exception as exc:  # never crash the pipeline on one URL
                last_err = f"error: {exc}"
            time.sleep(min(2 ** attempt, 8))

        page.status = "error"
        page.error_message = last_err or "unknown fetch error"
        page.runtime_seconds = round(time.time() - started, 3)
        log.info("FETCH FAIL %s (%s)", url, page.error_message)
        return page
