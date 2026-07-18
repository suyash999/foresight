"""Official Reddit Data API client (compliant).

Reddit's robots.txt disallows crawling comment pages, but the official OAuth
Data API is the sanctioned way to read the same public content. This client
authenticates with the app-only (client_credentials) grant and pulls hot posts
(and optional keyword search) from configured collector subreddits, returning
them as FetchedPage objects so the normal extraction / news / event pipeline can
process the post text directly — no page crawling required.

Disabled unless REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set and enabled in
config. Fully guarded: any auth/HTTP failure returns [] (pipeline unaffected).
"""
from __future__ import annotations

import time

import httpx

from ..logging_config import get_logger
from ..models import FetchedPage
from ..util import sha1, utc_iso

log = get_logger("scraping.reddit_api")


class RedditAPIClient:
    def __init__(self, config):
        conf = config.get("search_providers", {}).get("reddit_api", {})
        self.enabled = bool(conf.get("enabled", False))
        self.client_id = config.env(conf.get("client_id_env", "REDDIT_CLIENT_ID"))
        self.client_secret = config.env(conf.get("client_secret_env", "REDDIT_CLIENT_SECRET"))
        self.user_agent = conf.get("user_agent",
                                   "python:foresight-collectibles:1.0 (research)")
        self.limit_per_sub = int(conf.get("limit_per_subreddit", 25))
        self.max_subreddits = int(conf.get("max_subreddits_per_cycle", 22))
        self.subreddits = [
            (s.get("sub") if isinstance(s, dict) else s,
             s.get("verticals") if isinstance(s, dict) else None,
             s.get("source_type", "collector forum") if isinstance(s, dict) else "collector forum")
            for s in config.get("seed_reddit_subreddits", [])
        ]
        self._token = None
        self._client = httpx.Client(timeout=15, headers={"User-Agent": self.user_agent})

    def available(self) -> bool:
        return self.enabled and bool(self.client_id) and bool(self.client_secret)

    def _auth(self) -> bool:
        if self._token:
            return True
        try:
            resp = self._client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
            )
            if resp.status_code != 200:
                log.warning("Reddit auth failed HTTP %s: %s", resp.status_code, resp.text[:160])
                return False
            self._token = resp.json().get("access_token")
            return bool(self._token)
        except Exception as exc:
            log.warning("Reddit auth error: %s", exc)
            return False

    def fetch_pages(self) -> list[FetchedPage]:
        """Return one FetchedPage per recent post across collector subreddits."""
        if not self.available():
            return []
        if not self._auth():
            return []
        pages: list[FetchedPage] = []
        headers = {"Authorization": f"bearer {self._token}", "User-Agent": self.user_agent}
        for sub, verticals, stype in self.subreddits[: self.max_subreddits]:
            if not sub:
                continue
            try:
                resp = self._client.get(
                    f"https://oauth.reddit.com/r/{sub}/hot",
                    headers=headers, params={"limit": self.limit_per_sub, "raw_json": 1})
                if resp.status_code != 200:
                    log.debug("Reddit r/%s HTTP %s", sub, resp.status_code)
                    continue
                children = resp.json().get("data", {}).get("children", [])
            except Exception as exc:
                log.debug("Reddit r/%s error: %s", sub, exc)
                continue
            for child in children:
                d = child.get("data", {})
                title = d.get("title", "")
                selftext = d.get("selftext", "") or ""
                permalink = "https://www.reddit.com" + d.get("permalink", "")
                # link posts point to an external article/marketplace page
                ext = d.get("url_overridden_by_dest") or d.get("url", "")
                body = f"{title}\n{selftext}".strip()
                if ext and "reddit.com" not in ext and "redd.it" not in ext:
                    body += f"\nLinked: {ext}"
                if len(body) < 8:
                    continue
                created = d.get("created_utc")
                pub = None
                if created:
                    try:
                        from datetime import datetime, timezone
                        pub = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
                    except Exception:
                        pub = None
                page = FetchedPage(
                    url=permalink, domain="reddit.com", status="ok",
                    http_status=200, title=title[:300], text=body[:6000],
                    published_at=pub, extraction_method="reddit_api",
                    meta={"subreddit": sub, "verticals": verticals or [],
                          "external_url": ext, "source_type": stype},
                    fetched_at=utc_iso(), text_hash=sha1(body[:5000]),
                )
                pages.append(page)
            time.sleep(0.6)  # polite pacing under Reddit's rate limit
        log.info("Reddit API fetched %d posts from %d subreddits",
                 len(pages), min(len(self.subreddits), self.max_subreddits))
        return pages

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
