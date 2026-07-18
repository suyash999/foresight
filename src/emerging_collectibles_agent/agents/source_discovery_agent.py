"""Source Discovery Agent — high-recall, multi-channel URL discovery.

Channels (all external, eBay excluded, robots/compliance enforced downstream):
  - RSS feeds (feedparser)
  - Sitemaps (sitemap.xml / news sitemaps)
  - GDELT global news-event API (free, no key)
  - Wikipedia search API (free, no key)
  - Wikidata search API (free, no key)
  - Google News RSS keyword search (keyless, US) — searchable news firehose
  - Bing News RSS keyword search (keyless, US) — corroborating news source
  - Google Trends daily RSS (keyless, US) — earliest demand-side signal
  - Wikimedia Pageviews API (keyless) — attention/emergence + watchlist spike detection
  - Wikimedia EventStreams SSE (keyless, bounded read) — new-article signal
  - Shopify products.json (keyless) — real-time new-SKU discovery on brand stores
  - Scryfall / Pokemon TCG catalog APIs (keyless) — release calendars
  - Product Hunt RSS + Gamefound (keyless) — new product / crowdfunding launches
  - Compliant search APIs (Bing / SerpAPI) when configured
  - Recursive expansion from already-crawled credible pages

The demand/attention channels (Google Trends, Wikipedia pageviews, EventStreams)
are *leading* indicators — in the Labubu case they surfaced the trend 5-12 months
before mainstream news — so they run before the heavier per-query news loops.

Every discovered URL is logged with why it was selected/rejected so the
dashboard's "URL Sourcing Intelligence" panel is fully observable. Discovery
targets high recall: it iterates every vertical × query and enters a recovery
mode if too few URLs are found.
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlparse

import httpx

# A discovery channel gives up after this many consecutive network failures
# (treats the source as down for this cycle instead of timing out on every query).
_CIRCUIT_BREAK_FAILS = 3

from ..config import Config
from ..logging_config import get_logger
from ..models import DiscoveredURL
from ..taxonomy import map_category, map_vertical
from ..util import full_host, normalize_url, registered_domain

log = get_logger("agent.discovery")


class SourceDiscoveryAgent:
    def __init__(self, config: Config, memory=None):
        self.config = config
        self.memory = memory  # SelfLearningMemory (optional), for source quality
        ud = config.get("url_discovery", {})
        self.min_urls = int(ud.get("min_urls_per_cycle", 500))
        self.target_urls = int(ud.get("target_urls_per_cycle", 2000))
        self.max_urls = int(ud.get("max_urls_per_cycle", 10000))
        self.max_per_domain = int(ud.get("max_urls_per_domain_per_cycle", 100))
        self.relevance_threshold = float(ud.get("relevance_threshold", 0.15))
        self.ud = ud
        self.sp = config.get("search_providers", {})
        self.verticals = config.verticals
        self.queries = config.get("seed_search_queries", {})
        self.excluded = set(config.excluded_domains)
        self.allowed = set(config.allowed_domains)
        self.known_sources = config.get("known_sources", {})
        # fast-fail timeouts so a blocked/slow network never hangs a cycle
        self.max_discovery_seconds = float(ud.get("max_discovery_seconds", 120))
        self._client = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
            headers={"User-Agent": config.get("scraping.user_agent",
                                              "EmergingCollectiblesIntelligenceBot/1.0")},
        )
        # per-cycle accounting
        self._domain_counts: dict[str, int] = {}
        self._deadline: float = 0.0

    def _time_left(self) -> bool:
        return time.time() < self._deadline

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass

    # -- domain gating ------------------------------------------------------
    def _excluded_domain(self, domain: str) -> bool:
        d = (domain or "").lower()
        for ex in self.excluded:
            if d == ex or d.endswith("." + ex) or ex in d:
                return True
        if self.allowed:
            return not any(d == a or d.endswith("." + a) for a in self.allowed)
        return False

    def _source_type_for(self, domain: str, default: str) -> tuple[str, float]:
        info = self.known_sources.get(domain)
        if info:
            return info.get("source_type", default), float(info.get("credibility", 0.6))
        return default, 0.5

    def _make_url(self, url: str, method: str, source: str, source_type: str,
                  verticals_hint: Optional[list[str]] = None, seed_query: str = "",
                  seed_url: str = "", title: str = "", snippet: str = "",
                  published: str = "", depth: int = 0) -> Optional[DiscoveredURL]:
        url = normalize_url(url)
        if not url or not url.startswith("http"):
            return None
        domain = registered_domain(url)
        d = DiscoveredURL(
            url=url, domain=domain, discovery_method=method, discovery_source=source,
            seed_query=seed_query, seed_url=seed_url, title=title, snippet=snippet,
            published_at=published or None, depth=depth,
        )
        # domain gating
        if self._excluded_domain(domain):
            d.selection_status = "rejected"
            d.rejection_reason = "excluded_domain (eBay/external-only policy)"
            d.crawl_allowed = False
            return d
        if self._domain_counts.get(domain, 0) >= self.max_per_domain:
            d.selection_status = "rejected"
            d.rejection_reason = "max_urls_per_domain_per_cycle reached"
            return d
        # source type / credibility hint
        st, cred = self._source_type_for(domain, source_type)
        d.source_type = st
        d.credibility_score = cred
        # vertical mapping from title+snippet (fallback to hint)
        text = f"{title} {snippet}"
        vertical, vscore, _ = map_vertical(text, self.verticals)
        if not vertical and verticals_hint:
            vertical = verticals_hint[0]
            vscore = 0.35
        d.mapped_vertical = vertical
        d.mapped_category = map_category(text, vertical)
        d.relevance_score = round(max(vscore, 0.2 if verticals_hint else 0.0), 3)
        # priority: relevance * credibility, with source-quality memory nudge
        qual = 1.0
        if self.memory is not None:
            qual = self.memory.domain_priority_multiplier(domain)
        d.priority_score = round(d.relevance_score * (0.5 + 0.5 * cred) * qual, 4)
        # selection decision (high recall: low threshold)
        if d.relevance_score >= self.relevance_threshold or verticals_hint:
            d.selection_status = "selected"
            d.selection_reason = (
                f"relevance={d.relevance_score} vertical={vertical or 'n/a'} "
                f"source_type={st} cred={cred}"
            )
            self._domain_counts[domain] = self._domain_counts.get(domain, 0) + 1
        else:
            d.selection_status = "rejected"
            d.rejection_reason = f"relevance {d.relevance_score} < threshold {self.relevance_threshold}"
        return d

    # -- channel: RSS -------------------------------------------------------
    def _from_rss(self) -> list[DiscoveredURL]:
        if not self.ud.get("enable_rss_discovery", True) or not self.sp.get("rss", {}).get("enabled", True):
            return []
        out: list[DiscoveredURL] = []
        feeds = self.config.get("seed_rss_feeds", [])
        try:
            import feedparser
        except Exception:
            return out
        fails = 0
        for feed in feeds:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS + 2:
                break
            furl = feed.get("url") if isinstance(feed, dict) else feed
            st = feed.get("source_type", "news") if isinstance(feed, dict) else "news"
            vh = feed.get("verticals") if isinstance(feed, dict) else None
            try:
                resp = self._client.get(furl)
                parsed = feedparser.parse(resp.content)
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("RSS fetch failed %s: %s", furl, exc)
                continue
            for entry in parsed.entries[:60]:
                link = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", "")[:400]
                pub = entry.get("published", "") or entry.get("updated", "")
                du = self._make_url(link, "rss", furl, st, vh, title=title,
                                    snippet=summary, published=pub)
                if du:
                    out.append(du)
        log.info("RSS discovered %d urls from %d feeds", len(out), len(feeds))
        return out

    # -- channel: Reddit collector communities (public RSS, keyless) --------
    def _from_reddit(self) -> list[DiscoveredURL]:
        if not self.ud.get("enable_reddit_discovery", True) or \
                not self.sp.get("reddit", {}).get("enabled", True):
            return []
        # if the official Reddit API is enabled, it supersedes RSS crawl (avoids
        # crawling robots-blocked comment pages)
        if self.sp.get("reddit_api", {}).get("enabled", False):
            return []
        subs = self.config.get("seed_reddit_subreddits", [])
        if not subs:
            return []
        try:
            import feedparser
        except Exception:
            return []
        out: list[DiscoveredURL] = []
        fails = 0
        for entry in subs:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            sub = entry.get("sub") if isinstance(entry, dict) else entry
            st = entry.get("source_type", "collector forum") if isinstance(entry, dict) else "collector forum"
            vh = entry.get("verticals") if isinstance(entry, dict) else None
            rss_url = f"https://www.reddit.com/r/{sub}/.rss"
            try:
                resp = self._client.get(rss_url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; CollectiblesResearchBot/1.0)"})
                if resp.status_code == 429:
                    fails += 1
                    time.sleep(2.0)  # polite backoff on Reddit rate limit
                    continue
                if resp.status_code != 200:
                    fails += 1
                    continue
                parsed = feedparser.parse(resp.content)
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("Reddit RSS failed r/%s: %s", sub, exc)
                continue
            for item in parsed.entries[:40]:
                du = self._make_url(item.get("link", ""), "reddit", f"r/{sub}", st, vh,
                                    title=item.get("title", ""),
                                    snippet=item.get("summary", "")[:300],
                                    published=item.get("published", "") or item.get("updated", ""))
                if du:
                    out.append(du)
            time.sleep(1.2)  # stay under Reddit's rate limit
        log.info("Reddit discovered %d urls from %d subreddits", len(out), len(subs))
        return out

    # -- channel: YouTube (OFFICIAL channel RSS feeds, keyless) -------------
    def _from_youtube(self) -> list[DiscoveredURL]:
        if not self.ud.get("enable_youtube_discovery", True):
            return []
        channels = self.config.get("seed_youtube_channels", [])
        if not channels:
            return []
        try:
            import feedparser
        except Exception:
            return []
        out: list[DiscoveredURL] = []
        for ch in channels:
            if not self._time_left():
                break
            cid = ch.get("channel_id") if isinstance(ch, dict) else ch
            st = ch.get("source_type", "social/news aggregation") if isinstance(ch, dict) else "social/news aggregation"
            vh = ch.get("verticals") if isinstance(ch, dict) else None
            feed = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
            try:
                resp = self._client.get(feed)
                if resp.status_code != 200:
                    continue
                parsed = feedparser.parse(resp.content)
            except Exception as exc:
                log.debug("YouTube RSS failed %s: %s", cid, exc)
                continue
            for item in parsed.entries[:30]:
                du = self._make_url(item.get("link", ""), "youtube", cid, st, vh,
                                    title=item.get("title", ""),
                                    snippet=item.get("summary", "")[:300],
                                    published=item.get("published", ""))
                if du:
                    out.append(du)
        log.info("YouTube discovered %d urls from %d channels", len(out), len(channels))
        return out

    # -- channel: Hacker News (Algolia public API, keyless) -----------------
    def _from_hackernews(self, queries: list[tuple[str, str]]) -> list[DiscoveredURL]:
        if not self.ud.get("enable_hn_discovery", True) or \
                not self.sp.get("hackernews", {}).get("enabled", True):
            return []
        out: list[DiscoveredURL] = []
        fails = 0
        seen_q: set[str] = set()
        for vertical, query in queries:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            if query in seen_q:
                continue
            seen_q.add(query)
            try:
                resp = self._client.get("https://hn.algolia.com/api/v1/search",
                                        params={"query": query, "tags": "story", "hitsPerPage": 10})
                if resp.status_code != 200:
                    fails += 1
                    continue
                data = resp.json()
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("HN failed for %s: %s", query, exc)
                continue
            for hit in data.get("hits", []):
                url = hit.get("url") or (f"https://news.ycombinator.com/item?id={hit.get('objectID')}")
                du = self._make_url(url, "hackernews", query, "social/news aggregation", [vertical],
                                    seed_query=query, title=hit.get("title", ""),
                                    published=hit.get("created_at", ""))
                if du:
                    out.append(du)
        log.info("Hacker News discovered %d urls", len(out))
        return out

    # -- channel: sitemaps --------------------------------------------------
    def _from_sitemaps(self) -> list[DiscoveredURL]:
        if not self.ud.get("enable_sitemap_discovery", True):
            return []
        out: list[DiscoveredURL] = []
        sitemaps = self.config.get("seed_sitemaps", [])
        for sm in sitemaps:
            if not self._time_left():
                break
            surl = sm.get("url") if isinstance(sm, dict) else sm
            st = sm.get("source_type", "unknown") if isinstance(sm, dict) else "unknown"
            vh = sm.get("verticals") if isinstance(sm, dict) else None
            try:
                resp = self._client.get(surl)
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(resp.content, "xml")
                locs = [l.get_text() for l in soup.find_all("loc")][:200]
            except Exception as exc:
                log.debug("sitemap fetch failed %s: %s", surl, exc)
                continue
            for loc in locs:
                du = self._make_url(loc, "sitemap", surl, st, vh)
                if du:
                    out.append(du)
        log.info("Sitemap discovered %d urls", len(out))
        return out

    # -- channel: GDELT -----------------------------------------------------
    def _from_gdelt(self, queries: list[tuple[str, str]]) -> list[DiscoveredURL]:
        if not self.ud.get("enable_gdelt_discovery", True) or not self.sp.get("gdelt", {}).get("enabled", True):
            return []
        out: list[DiscoveredURL] = []
        base = "https://api.gdeltproject.org/api/v2/doc/doc"
        fails = 0
        for vertical, query in queries:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            params = {
                "query": query + " sourcelang:english",
                "mode": "ArtList",
                "maxrecords": "40",
                "format": "json",
                "sort": "DateDesc",
            }
            try:
                resp = self._client.get(base, params=params)
                if resp.status_code != 200:
                    fails += 1
                    continue
                data = resp.json()
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("GDELT failed for %s: %s", query, exc)
                continue
            for art in data.get("articles", [])[:40]:
                url = art.get("url", "")
                title = art.get("title", "")
                pub = art.get("seendate", "")
                du = self._make_url(url, "gdelt", query, "general news", [vertical],
                                    seed_query=query, title=title, published=pub)
                if du:
                    out.append(du)
            time.sleep(0.3)  # be polite to GDELT
        log.info("GDELT discovered %d urls", len(out))
        return out

    # -- channel: Wikipedia -------------------------------------------------
    def _from_wikipedia(self, queries: list[tuple[str, str]]) -> list[DiscoveredURL]:
        if not self.ud.get("enable_wikipedia_discovery", True) or not self.sp.get("wikipedia", {}).get("enabled", True):
            return []
        out: list[DiscoveredURL] = []
        api = "https://en.wikipedia.org/w/api.php"
        fails = 0
        for vertical, query in queries:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            params = {
                "action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": "10",
            }
            try:
                resp = self._client.get(api, params=params)
                if resp.status_code != 200:
                    fails += 1
                    continue
                data = resp.json()
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("Wikipedia failed for %s: %s", query, exc)
                continue
            for hit in data.get("query", {}).get("search", []):
                title = hit.get("title", "")
                page_url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
                snippet = hit.get("snippet", "")[:300]
                du = self._make_url(page_url, "wikipedia", query, "social/news aggregation",
                                    [vertical], seed_query=query, title=title, snippet=snippet)
                if du:
                    out.append(du)
        log.info("Wikipedia discovered %d urls", len(out))
        return out

    # -- channel: Wikidata --------------------------------------------------
    def _from_wikidata(self, queries: list[tuple[str, str]]) -> list[DiscoveredURL]:
        if not self.ud.get("enable_wikidata_discovery", True) or not self.sp.get("wikidata", {}).get("enabled", True):
            return []
        out: list[DiscoveredURL] = []
        api = "https://www.wikidata.org/w/api.php"
        fails = 0
        for vertical, query in queries[:20]:  # wikidata entity search is coarse
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            params = {
                "action": "wbsearchentities", "search": query, "language": "en",
                "format": "json", "limit": "5",
            }
            try:
                resp = self._client.get(api, params=params)
                if resp.status_code != 200:
                    fails += 1
                    continue
                data = resp.json()
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("Wikidata failed for %s: %s", query, exc)
                continue
            for hit in data.get("search", []):
                concept = hit.get("concepturi", "")
                label = hit.get("label", "")
                desc = hit.get("description", "")
                du = self._make_url(concept, "wikidata", query, "social/news aggregation",
                                    [vertical], seed_query=query, title=label, snippet=desc)
                if du:
                    out.append(du)
        log.info("Wikidata discovered %d urls", len(out))
        return out

    # -- channel: compliant search APIs -------------------------------------
    def _from_search_apis(self, queries: list[tuple[str, str]]) -> list[DiscoveredURL]:
        if not self.ud.get("enable_search_api_discovery", True):
            return []
        out: list[DiscoveredURL] = []
        out += self._bing(queries)
        out += self._serpapi(queries)
        return out

    def _bing(self, queries) -> list[DiscoveredURL]:
        conf = self.sp.get("bing", {})
        if not conf.get("enabled"):
            return []
        key = self.config.env(conf.get("api_key_env", "BING_SEARCH_API_KEY"))
        if not key:
            return []
        cap = int(conf.get("max_queries_per_cycle", 20))
        endpoint = conf.get("endpoint", "https://api.bing.microsoft.com/v7.0/search")
        out, fails = [], 0
        for vertical, query in queries[:cap]:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            try:
                resp = self._client.get(
                    endpoint,
                    params={"q": query, "count": 10},
                    headers={"Ocp-Apim-Subscription-Key": key},
                )
                if resp.status_code != 200:
                    fails += 1
                    log.debug("Bing HTTP %s for %s", resp.status_code, query)
                    continue
                data = resp.json()
                fails = 0
            except Exception:
                fails += 1
                continue
            for item in data.get("webPages", {}).get("value", []):
                du = self._make_url(item.get("url", ""), "search_api", "bing",
                                    "unknown", [vertical], seed_query=query,
                                    title=item.get("name", ""), snippet=item.get("snippet", ""))
                if du:
                    out.append(du)
        return out

    def _serpapi(self, queries) -> list[DiscoveredURL]:
        conf = self.sp.get("serpapi", {})
        if not conf.get("enabled"):
            return []
        key = self.config.env(conf.get("api_key_env", "SERPAPI_API_KEY"))
        if not key:
            return []
        out = []
        for vertical, query in queries:
            try:
                resp = self._client.get("https://serpapi.com/search",
                                        params={"q": query, "api_key": key, "num": 10})
                data = resp.json()
            except Exception:
                continue
            for item in data.get("organic_results", []):
                du = self._make_url(item.get("link", ""), "search_api", "serpapi",
                                    "unknown", [vertical], seed_query=query,
                                    title=item.get("title", ""), snippet=item.get("snippet", ""))
                if du:
                    out.append(du)
        return out

    # -- channel: Google News RSS (keyless, keyword-searchable, US) ----------
    @staticmethod
    def _decode_gnews_url(gurl: str) -> str:
        """Best-effort decode of a Google News redirect link to the real article.

        Google News RSS <link> values are base64-ish redirect URLs. Older-format
        links embed the real URL and decode cleanly; newer ID-only links do not,
        in which case we return "" and the item is skipped (keeps crawl clean).
        """
        try:
            if "news.google.com" not in gurl:
                return gurl
            seg = gurl.split("/articles/")[-1].split("/read/")[-1].split("?")[0].split("/")[-1]
            seg += "=" * (-len(seg) % 4)
            raw = base64.urlsafe_b64decode(seg)
            # Walk protobuf wire format and return the length-delimited field whose
            # value is a URL (read exactly `ln` bytes, so no trailing marker noise).
            idx, n = 0, len(raw)
            while idx < n:
                wire = raw[idx] & 0x07
                idx += 1
                if wire == 2:  # length-delimited
                    ln, shift = 0, 0
                    while idx < n:
                        b = raw[idx]
                        idx += 1
                        ln |= (b & 0x7F) << shift
                        if not (b & 0x80):
                            break
                        shift += 7
                    val = raw[idx:idx + ln].decode("latin-1", errors="ignore")
                    idx += ln
                    if val.startswith(("http://", "https://")):
                        return val
                elif wire == 0:  # varint field — skip
                    while idx < n and raw[idx] & 0x80:
                        idx += 1
                    idx += 1
                else:
                    break
            # fallback: regex scan of the decoded bytes
            m = re.search(r'https?://[^\x00-\x1f"\\\s]+', raw.decode("latin-1", errors="ignore"))
            if m:
                return m.group(0)
        except Exception:
            pass
        return ""

    def _from_google_news(self, queries) -> list[DiscoveredURL]:
        conf = self.sp.get("google_news", {})
        if not self.ud.get("enable_google_news_discovery", True) or not conf.get("enabled", True):
            return []
        try:
            import feedparser
        except Exception:
            return []
        cap = int(conf.get("max_queries_per_cycle", 30))
        window = conf.get("window", "7d")
        out, fails = [], 0
        for vertical, query in queries[:cap]:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            try:
                resp = self._client.get(
                    "https://news.google.com/rss/search",
                    params={"q": f"{query} when:{window}", "hl": "en-US",
                            "gl": "US", "ceid": "US:en"})
                if resp.status_code != 200:
                    fails += 1
                    continue
                parsed = feedparser.parse(resp.content)
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("Google News failed for %s: %s", query, exc)
                continue
            for e in parsed.entries[:20]:
                real = self._decode_gnews_url(e.get("link", ""))
                if not real or "news.google.com" in real:
                    continue
                du = self._make_url(real, "google_news", query, "general news", [vertical],
                                    seed_query=query, title=e.get("title", ""),
                                    snippet=e.get("summary", "")[:300],
                                    published=e.get("published", "") or e.get("updated", ""))
                if du:
                    out.append(du)
            time.sleep(0.2)
        log.info("Google News discovered %d urls", len(out))
        return out

    # -- channel: Bing News RSS (keyless, keyword-searchable, US) ------------
    def _bing_news_fetch(self, queries, method: str, source_type: str,
                         cap: Optional[int] = None) -> list[DiscoveredURL]:
        """Core Bing News RSS keyword search (no enable-gate; reusable)."""
        try:
            import feedparser
        except Exception:
            return []
        cap = cap if cap is not None else len(queries)
        out, fails = [], 0
        for vertical, query in queries[:cap]:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            try:
                resp = self._client.get(
                    "https://www.bing.com/news/search",
                    params={"q": query, "format": "rss", "setmkt": "en-US", "cc": "US"})
                if resp.status_code != 200:
                    fails += 1
                    continue
                parsed = feedparser.parse(resp.content)
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("Bing News failed for %s: %s", query, exc)
                continue
            for e in parsed.entries[:15]:
                link = e.get("link", "")
                # Bing wraps the real article in an apiclick redirect: pull the
                # real publisher URL out of the `url` query param.
                if "bing.com" in link:
                    link = parse_qs(urlparse(link).query).get("url", [""])[0]
                if not link or "bing.com" in link:
                    continue
                du = self._make_url(link, method, query, source_type, [vertical],
                                    seed_query=query, title=e.get("title", ""),
                                    snippet=e.get("summary", "")[:300],
                                    published=e.get("published", "") or e.get("updated", ""))
                if du:
                    out.append(du)
            time.sleep(0.2)
        return out

    def _from_bing_news(self, queries) -> list[DiscoveredURL]:
        conf = self.sp.get("bing_news", {})
        if not self.ud.get("enable_bing_news_discovery", True) or not conf.get("enabled", True):
            return []
        cap = int(conf.get("max_queries_per_cycle", 30))
        out = self._bing_news_fetch(queries, "bing_news", "general news", cap)
        log.info("Bing News discovered %d urls", len(out))
        return out

    # -- channel: Google Trends daily RSS (keyless, US demand signal) --------
    def _from_google_trends(self) -> list[DiscoveredURL]:
        conf = self.sp.get("google_trends", {})
        if not self.ud.get("enable_google_trends_discovery", True) or not conf.get("enabled", True):
            return []
        out: list[DiscoveredURL] = []
        try:
            from bs4 import BeautifulSoup
            resp = self._client.get("https://trends.google.com/trending/rss",
                                    params={"geo": conf.get("geo", "US")})
            if resp.status_code != 200:
                log.debug("Google Trends HTTP %s", resp.status_code)
                return []
            soup = BeautifulSoup(resp.content, "xml")
        except Exception as exc:
            log.debug("Google Trends failed: %s", exc)
            return []
        for item in soup.find_all("item"):
            term = (item.find("title").get_text() if item.find("title") else "").strip()
            if not term:
                continue
            traf = item.find("ht:approx_traffic")
            traffic = traf.get_text() if traf else ""
            news_urls = [n.get_text() for n in item.find_all("ht:news_item_url")]
            news_titles = [n.get_text() for n in item.find_all("ht:news_item_title")]
            snippet = f"trending US search term; approx_traffic={traffic}"
            # emit the real news article urls behind each trending term
            for i, nu in enumerate(news_urls):
                nt = news_titles[i] if i < len(news_titles) else term
                # no vertical hint: the relevance gate keeps only collectible-relevant trends
                du = self._make_url(nu, "google_trends", term, "trend signal", None,
                                    seed_query=term, title=f"{term} — {nt}", snippet=snippet)
                if du and du.selection_status == "selected":
                    du.priority_score = round(du.priority_score * 1.2, 4)
                    out.append(du)
        log.info("Google Trends discovered %d urls", len(out))
        return out

    # -- channel: Wikimedia Pageviews (keyless attention/emergence) ----------
    def _from_wikipedia_pageviews(self) -> list[DiscoveredURL]:
        conf = self.sp.get("wikipedia_pageviews", {})
        if not self.ud.get("enable_wikipedia_pageviews_discovery", True) or not conf.get("enabled", True):
            return []
        out: list[DiscoveredURL] = []
        ua = {"User-Agent": self.config.get("scraping.user_agent",
                                            "EmergingCollectiblesIntelligenceBot/1.0")}
        base = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
        # (a) daily top-viewed leaderboard — a spiking article = an emerging topic
        try:
            d = datetime.now(timezone.utc) - timedelta(days=int(conf.get("top_lag_days", 2)))
            resp = self._client.get(f"{base}/top/en.wikipedia/all-access/{d:%Y/%m/%d}",
                                    headers=ua)
            if resp.status_code == 200:
                arts = (resp.json().get("items", [{}]) or [{}])[0].get("articles", [])
                for a in arts[: int(conf.get("top_limit", 200))]:
                    title = a.get("article", "")
                    if not title or title == "Main_Page" or ":" in title:
                        continue
                    page = f"https://en.wikipedia.org/wiki/{title}"
                    du = self._make_url(
                        page, "wiki_pageviews_top", f"rank{a.get('rank')}",
                        "social/news aggregation", None,
                        title=title.replace("_", " "),
                        snippet=f"top-viewed Wikipedia article, {a.get('views', 0)} views/day")
                    if du and du.selection_status == "selected":
                        du.priority_score = round(du.priority_score * 1.3, 4)
                        out.append(du)
            else:
                log.debug("Wiki pageviews top HTTP %s", resp.status_code)
        except Exception as exc:
            log.debug("Wiki pageviews top failed: %s", exc)
        # (b) per-article spike detection on a brand/product watchlist (the Labubu
        #     early-warning: pageviews led mainstream news by 5-8 months)
        watch = self.config.get("wikipedia_watchlist", []) or []
        spike_ratio = float(conf.get("spike_ratio", 2.0))
        spike_min = float(conf.get("spike_min_views", 50))
        end = datetime.now(timezone.utc) - timedelta(days=1)
        start = end - timedelta(days=30)
        for w in watch:
            if not self._time_left():
                break
            title = w.get("article") if isinstance(w, dict) else w
            vh = w.get("verticals") if isinstance(w, dict) else None
            if not title:
                continue
            art = title.replace(" ", "_")
            try:
                resp = self._client.get(
                    f"{base}/per-article/en.wikipedia/all-access/user/{art}/daily/"
                    f"{start:%Y%m%d}/{end:%Y%m%d}", headers=ua)
                if resp.status_code != 200:
                    continue
                vals = [i.get("views", 0) for i in resp.json().get("items", [])]
            except Exception as exc:
                log.debug("Wiki pageviews spike failed %s: %s", title, exc)
                continue
            if len(vals) < 14:
                continue
            recent = sum(vals[-7:]) / 7.0
            prior = sum(vals[:-7]) / max(1, len(vals) - 7)
            if prior > 0 and recent / prior >= spike_ratio and recent >= spike_min:
                ratio = round(recent / prior, 1)
                du = self._make_url(
                    f"https://en.wikipedia.org/wiki/{art}", "wiki_pageviews_spike",
                    title, "social/news aggregation", vh, title=title,
                    snippet=f"pageview spike x{ratio} (7d avg {int(recent)} vs prior {int(prior)})")
                if du:
                    du.priority_score = round(du.priority_score * 1.5 + 0.1, 4)
                    out.append(du)
        log.info("Wikipedia pageviews discovered %d urls", len(out))
        return out

    # -- channel: Wikimedia EventStreams (keyless real-time, bounded read) ---
    def _from_wikimedia_eventstream(self) -> list[DiscoveredURL]:
        conf = self.sp.get("wikimedia_eventstream", {})
        if not self.ud.get("enable_eventstream_discovery", True) or not conf.get("enabled", True):
            return []
        seconds = float(conf.get("read_seconds", 6))
        out, seen = [], set()
        ua = {"User-Agent": self.config.get("scraping.user_agent",
                                            "EmergingCollectiblesIntelligenceBot/1.0"),
              "Accept": "text/event-stream"}
        deadline = time.time() + seconds
        try:
            with self._client.stream(
                    "GET", "https://stream.wikimedia.org/v2/stream/recentchange",
                    headers=ua, timeout=httpx.Timeout(seconds + 5, connect=5.0)) as r:
                if r.status_code != 200:
                    log.debug("EventStream HTTP %s", r.status_code)
                    return []
                for line in r.iter_lines():
                    if time.time() > deadline:
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if ev.get("wiki") != "enwiki" or ev.get("type") != "new" \
                            or ev.get("namespace") != 0:
                        continue
                    title = ev.get("title", "")
                    if not title or title in seen or ":" in title:
                        continue
                    seen.add(title)
                    du = self._make_url(
                        f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                        "eventstream", "recentchange:new", "social/news aggregation",
                        None, title=title, snippet="newly created English Wikipedia article")
                    if du and du.selection_status == "selected":
                        out.append(du)
        except Exception as exc:
            log.debug("EventStream failed: %s", exc)
        log.info("Wikimedia EventStream discovered %d new-article urls", len(out))
        return out

    # -- channel: Shopify products.json (keyless new-SKU discovery) ----------
    def _from_shopify(self) -> list[DiscoveredURL]:
        conf = self.sp.get("shopify", {})
        if not self.ud.get("enable_shopify_discovery", True) or not conf.get("enabled", True):
            return []
        stores = self.config.get("seed_shopify_stores", []) or []
        per = int(conf.get("max_products_per_store", 60))
        out, fails = [], 0
        for s in stores:
            if not self._time_left() or fails >= _CIRCUIT_BREAK_FAILS:
                break
            surl = (s.get("url") if isinstance(s, dict) else s or "").rstrip("/")
            st = s.get("source_type", "official manufacturer") if isinstance(s, dict) else "official manufacturer"
            vh = s.get("verticals") if isinstance(s, dict) else None
            if not surl:
                continue
            try:
                resp = self._client.get(f"{surl}/products.json",
                                        params={"limit": min(per, 250), "page": 1})
                if resp.status_code != 200:
                    fails += 1
                    continue
                prods = resp.json().get("products", [])
                fails = 0
            except Exception as exc:
                fails += 1
                log.debug("Shopify %s failed: %s", surl, exc)
                continue
            for p in prods[:per]:
                handle = p.get("handle", "")
                if not handle:
                    continue
                tags = p.get("tags", [])
                tagtxt = ", ".join(tags) if isinstance(tags, list) else str(tags)
                snippet = f"{p.get('vendor', '')} {p.get('product_type', '')} {tagtxt}".strip()[:300]
                du = self._make_url(
                    f"{surl}/products/{handle}", "shopify", surl, st, vh, seed_url=surl,
                    title=p.get("title", ""), snippet=snippet,
                    published=p.get("published_at") or p.get("created_at") or "")
                if du:
                    out.append(du)
            time.sleep(0.3)
        log.info("Shopify discovered %d urls from %d stores", len(out), len(stores))
        return out

    # -- channel: Scryfall MTG set release calendar (keyless) ---------------
    def _from_scryfall(self) -> list[DiscoveredURL]:
        conf = self.sp.get("scryfall", {})
        if not self.ud.get("enable_scryfall_discovery", True) or not conf.get("enabled", True):
            return []
        out = []
        try:
            resp = self._client.get("https://api.scryfall.com/sets")
            if resp.status_code != 200:
                return []
            sets = resp.json().get("data", [])
        except Exception as exc:
            log.debug("Scryfall failed: %s", exc)
            return []
        back = int(conf.get("window_days_back", 120))
        fwd = int(conf.get("window_days_forward", 365))
        today = datetime.now(timezone.utc).date()
        for s in sets:
            rd = s.get("released_at")
            if not rd:
                continue
            try:
                delta = (datetime.strptime(rd, "%Y-%m-%d").date() - today).days
            except Exception:
                continue
            if delta > fwd or delta < -back:
                continue
            url = s.get("scryfall_uri") or f"https://scryfall.com/sets/{s.get('code', '')}"
            tag = "upcoming" if delta > 0 else "recent"
            du = self._make_url(
                url, "scryfall", s.get("name", ""), "release calendar", ["Trading Cards"],
                title=f"MTG set: {s.get('name', '')}",
                snippet=f"{tag} {s.get('set_type', '')} set, {s.get('card_count', 0)} cards, releases {rd}",
                published=rd)
            if du:
                if delta > 0:
                    du.priority_score = round(du.priority_score * 1.3, 4)
                out.append(du)
        log.info("Scryfall discovered %d urls", len(out))
        return out

    # -- channel: Pokemon TCG set feed (keyless; news-amplified) -------------
    def _from_pokemontcg(self) -> list[DiscoveredURL]:
        conf = self.sp.get("pokemontcg", {})
        if not self.ud.get("enable_pokemontcg_discovery", True) or not conf.get("enabled", True):
            return []
        headers = {}
        key = self.config.env(conf.get("api_key_env", "POKEMONTCG_API_KEY"))
        if key:
            headers["X-Api-Key"] = key
        try:
            resp = self._client.get("https://api.pokemontcg.io/v2/sets",
                                    params={"orderBy": "-releaseDate", "pageSize": 20},
                                    headers=headers)
            if resp.status_code != 200:
                log.debug("Pokemon TCG HTTP %s", resp.status_code)
                return []
            sets = resp.json().get("data", [])
        except Exception as exc:
            log.debug("Pokemon TCG failed: %s", exc)
            return []
        # pokemontcg.io has no crawlable per-set page, so treat new sets as query
        # terms and find crawlable news coverage of them (real article urls).
        back = int(conf.get("window_days_back", 120))
        today = datetime.now(timezone.utc).date()
        names = []
        for s in sets:
            try:
                d = datetime.strptime(s.get("releaseDate", ""), "%Y/%m/%d").date()
                if (today - d).days > back:
                    continue
            except Exception:
                pass
            nm = s.get("name", "")
            if nm:
                names.append(("Trading Cards", f"Pokemon {nm} TCG set"))
        if not names:
            log.info("Pokemon TCG: no recent sets")
            return []
        out = self._bing_news_fetch(names, "pokemontcg", "release calendar", cap=len(names))
        log.info("Pokemon TCG discovered %d urls (via news for %d new sets)", len(out), len(names))
        return out

    # -- channel: Product Hunt launch feed (keyless) ------------------------
    def _from_producthunt(self) -> list[DiscoveredURL]:
        conf = self.sp.get("producthunt", {})
        if not self.ud.get("enable_producthunt_discovery", True) or not conf.get("enabled", True):
            return []
        try:
            import feedparser
        except Exception:
            return []
        out = []
        try:
            resp = self._client.get("https://www.producthunt.com/feed")
            if resp.status_code != 200:
                return []
            parsed = feedparser.parse(resp.content)
        except Exception as exc:
            log.debug("Product Hunt failed: %s", exc)
            return []
        for e in parsed.entries[: int(conf.get("max_items", 50))]:
            # no vertical hint: relevance gate keeps only collectible-relevant launches
            du = self._make_url(e.get("link", ""), "producthunt", "feed", "product launch",
                                None, title=e.get("title", ""),
                                snippet=e.get("summary", "")[:300],
                                published=e.get("published", "") or e.get("updated", ""))
            if du and du.selection_status == "selected":
                out.append(du)
        log.info("Product Hunt discovered %d urls", len(out))
        return out

    # -- channel: Gamefound crowdfunding (keyless; emerging by definition) ---
    def _from_gamefound(self) -> list[DiscoveredURL]:
        conf = self.sp.get("gamefound", {})
        if not self.ud.get("enable_gamefound_discovery", True) or not conf.get("enabled", True):
            return []
        endpoint = conf.get("endpoint", "https://gamefound.com/api/public/projects")
        out = []
        try:
            resp = self._client.get(endpoint, params={"sortBy": "campaignStartDate", "take": 40})
            if resp.status_code != 200:
                log.debug("Gamefound HTTP %s", resp.status_code)
                return []
            data = resp.json()
        except Exception as exc:
            log.debug("Gamefound failed: %s", exc)
            return []
        items = data.get("projects") or data.get("items") or (data if isinstance(data, list) else [])
        for p in items[:40]:
            if not isinstance(p, dict):
                continue
            url = p.get("url") or p.get("projectUrl") or ""
            if url.startswith("/"):
                url = "https://gamefound.com" + url
            if not url:
                continue
            du = self._make_url(url, "gamefound", p.get("name") or p.get("title", ""),
                                "crowdfunding", ["Toys and Hobbies"],
                                title=p.get("name") or p.get("title", ""),
                                snippet=(p.get("shortDescription") or "")[:300],
                                published=p.get("campaignStartDate") or "")
            if du:
                out.append(du)
        log.info("Gamefound discovered %d urls", len(out))
        return out

    # -- channel: seed URLs -------------------------------------------------
    def _from_seeds(self) -> list[DiscoveredURL]:
        out = []
        for seed in self.config.get("seed_urls", []):
            surl = seed.get("url") if isinstance(seed, dict) else seed
            st = seed.get("source_type", "unknown") if isinstance(seed, dict) else "unknown"
            vh = seed.get("verticals") if isinstance(seed, dict) else None
            du = self._make_url(surl, "seed", "config", st, vh)
            if du:
                out.append(du)
        return out

    # -- recursive expansion ------------------------------------------------
    def expand_links(self, links: Iterable[str], parent_url: str, parent_vertical: str,
                     depth: int) -> list[DiscoveredURL]:
        max_depth = int(self.ud.get("max_recursion_depth", 3))
        if depth > max_depth or not self.ud.get("enable_recursive_expansion", True):
            return []
        out = []
        parent_domain = registered_domain(parent_url)
        for link in links:
            du = self._make_url(link, "recursive", parent_url, "unknown",
                                [parent_vertical] if parent_vertical else None,
                                seed_url=parent_url, depth=depth)
            if du and du.selection_status == "selected":
                # slightly downweight same-domain link farms
                if registered_domain(link) == parent_domain:
                    du.priority_score *= 0.7
                out.append(du)
        return out

    # -- query set builder --------------------------------------------------
    def _query_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for vertical in self.verticals:
            for q in self.queries.get(vertical, []):
                pairs.append((vertical, q))
        return pairs

    def _expanded_query_pairs(self) -> list[tuple[str, str]]:
        """Recovery mode: broaden queries with extra modifiers."""
        modifiers = ["news", "release date", "auction", "sold out", "preorder",
                     "limited edition", "trending 2026", "market"]
        pairs = []
        for vertical in self.verticals:
            base_terms = self.queries.get(vertical, [vertical.lower()])
            for term in base_terms[:3]:
                for mod in modifiers:
                    pairs.append((vertical, f"{term} {mod}"))
        return pairs

    # -- main entrypoint ----------------------------------------------------
    def discover(self, recovery: bool = True) -> list[DiscoveredURL]:
        self._domain_counts = {}
        self._deadline = time.time() + self.max_discovery_seconds
        results: list[DiscoveredURL] = []
        queries = self._query_pairs()

        results += self._from_seeds()
        results += self._from_rss()
        results += self._from_reddit()
        results += self._from_youtube()
        # leading demand/attention + catalog signals first (few requests each,
        # highest early-warning value) so they always run within the time budget
        results += self._from_google_trends()
        results += self._from_wikipedia_pageviews()
        results += self._from_wikimedia_eventstream()
        results += self._from_shopify()
        results += self._from_scryfall()
        results += self._from_pokemontcg()
        results += self._from_producthunt()
        results += self._from_gamefound()
        # heavier per-query loops
        results += self._from_hackernews(queries)
        results += self._from_sitemaps()
        results += self._from_gdelt(queries)
        results += self._from_google_news(queries)
        results += self._from_bing_news(queries)
        results += self._from_wikipedia(queries)
        results += self._from_wikidata(queries)
        results += self._from_search_apis(queries)

        selected = [r for r in results if r.selection_status == "selected"]
        log.info("Discovery pass 1: %d total, %d selected", len(results), len(selected))

        # Recovery mode if under min threshold
        if recovery and len(selected) < self.min_urls:
            log.warning("URL discovery recovery mode: %d < min %d — expanding queries",
                        len(selected), self.min_urls)
            exp = self._expanded_query_pairs()
            results += self._from_gdelt(exp)
            results += self._from_google_news(exp)
            results += self._from_bing_news(exp)
            results += self._from_wikipedia(exp)
            results += self._from_search_apis(exp)

        # Deduplicate by URL, keep highest priority
        deduped = self._dedupe(results)
        # cap
        selected_final = [r for r in deduped if r.selection_status == "selected"]
        if len(selected_final) > self.max_urls:
            selected_final.sort(key=lambda r: r.priority_score, reverse=True)
            keep = set(id(r) for r in selected_final[: self.max_urls])
            for r in deduped:
                if r.selection_status == "selected" and id(r) not in keep:
                    r.selection_status = "rejected"
                    r.rejection_reason = "over max_urls_per_cycle (kept top-priority)"
        return deduped

    def _dedupe(self, urls: list[DiscoveredURL]) -> list[DiscoveredURL]:
        seen: dict[str, DiscoveredURL] = {}
        order: list[DiscoveredURL] = []
        for u in urls:
            key = u.url
            if key in seen:
                u.duplicate_flag = True
                u.selection_status = "duplicate"
                u.rejection_reason = "duplicate url"
                # keep best priority on the retained one
                if u.priority_score > seen[key].priority_score:
                    seen[key].priority_score = u.priority_score
                order.append(u)
            else:
                seen[key] = u
                order.append(u)
        return order

    # -- coverage metrics ---------------------------------------------------
    def coverage(self, urls: list[DiscoveredURL]) -> dict:
        selected = [u for u in urls if u.selection_status == "selected"]
        verticals_hit = {u.mapped_vertical for u in selected if u.mapped_vertical}
        source_types = {u.source_type for u in selected if u.source_type}
        vc = len(verticals_hit) / max(1, len(self.verticals))
        return {
            "discovered": len(urls),
            "selected": len(selected),
            "vertical_coverage_score": round(vc, 3),
            "verticals_hit": sorted(verticals_hit),
            "unique_source_types": len(source_types),
            "url_diversity_score": round(len(source_types) / 12.0, 3),
        }
