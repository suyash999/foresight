"""robots.txt compliance.

When `respect_robots_txt` is enabled in config, every fetch is gated by this
checker. robots.txt files are fetched once per host and cached. On any error
fetching robots.txt we fail OPEN (allow) but log it, except we never override
an explicit disallow.
"""
from __future__ import annotations

import time
import urllib.robotparser
from typing import Optional
from urllib.parse import urlparse

from ..logging_config import get_logger

log = get_logger("scraping.robots")


class RobotsCache:
    def __init__(self, user_agent: str, enabled: bool = True, ttl_seconds: int = 3600):
        self.user_agent = user_agent
        self.enabled = enabled
        self.ttl = ttl_seconds
        self._cache: dict[str, tuple[float, Optional[urllib.robotparser.RobotFileParser]]] = {}

    def _robots_url(self, url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}/robots.txt"

    def _get_parser(self, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        host = urlparse(url).netloc
        now = time.time()
        cached = self._cache.get(host)
        if cached and (now - cached[0]) < self.ttl:
            return cached[1]
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(self._robots_url(url))
        try:
            rp.read()
        except Exception as exc:
            log.debug("robots.txt unreadable for %s (%s); failing open", host, exc)
            rp = None  # type: ignore
        self._cache[host] = (now, rp)
        return rp

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        try:
            rp = self._get_parser(url)
            if rp is None:
                return True  # fail open when robots.txt cannot be read
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def crawl_delay(self, url: str) -> Optional[float]:
        if not self.enabled:
            return None
        rp = self._get_parser(url)
        if rp is None:
            return None
        try:
            return rp.crawl_delay(self.user_agent)
        except Exception:
            return None
