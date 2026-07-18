"""Orchestration Agent — the continuous agentic loop.

State machine per cycle:
  DISCOVER_SOURCES → VERIFY_SOURCES → SCORE_PAGES → PRIORITIZE_QUEUE →
  CRAWL_PAGES → EXTRACT_PRODUCTS → MAP_NEWS_TO_PRODUCTS → DISCOVER_EVENTS →
  NORMALIZE_PRODUCTS → SCORE_TRENDS → VALIDATE_TRENDS → GENERATE_REASONS →
  SAVE_RESULTS → REFRESH_DASHBOARD → SLEEP → REPEAT

High recall: broad discovery, generous extraction, transparent scoring, no
early aggressive filtering. Responsible: robots, rate limits, backoff, blocked
pages handled gracefully. Self-healing: failures trigger the recovery ladder
before decay; source-quality memory shapes future priority.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Optional
from urllib.parse import urlparse

from .config import Config
from .db import Database
from .exports import Exporter, build_product_row
from .llm.client import LLMClient
from .logging_config import get_logger
from .models import DiscoveredURL, FetchedPage
from .util import now_utc, registered_domain, sha1, utc_iso

from .agents.source_discovery_agent import SourceDiscoveryAgent
from .agents.source_verification_agent import SourceVerificationAgent
from .agents.page_scoring_agent import PageScoringAgent
from .agents.scraping_agent import ScrapingAgent, classify_failure
from .agents.product_extraction_agent import ProductExtractionAgent
from .agents.news_intelligence_agent import NewsIntelligenceAgent
from .agents.event_discovery_agent import EventDiscoveryAgent
from .agents.product_normalization_agent import ProductNormalizationAgent
from .agents.trend_scoring_agent import TrendScoringAgent
from .agents.trend_reasoning_agent import TrendReasoningAgent
from .agents.credibility_validation_agent import CredibilityValidationAgent
from .agents.failure_agent import FailureAgent
from .agents.self_learning_memory import SelfLearningMemory
from .agents.adaptive_strategy_router import AdaptiveStrategyRouter
from .agents.self_serve_recovery_agent import SelfServeRecoveryAgent
from .scraping.reddit_api import RedditAPIClient

log = get_logger("orchestrator")


class Orchestrator:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.database_path,
                           journal_mode=config.get("database_journal_mode", "DELETE"))
        self.memory = SelfLearningMemory(self.db, config)
        self.llm = LLMClient(config)

        self.discovery = SourceDiscoveryAgent(config, self.memory)
        self.verifier = SourceVerificationAgent(config, self.memory)
        self.page_scorer = PageScoringAgent(config)
        self.router = AdaptiveStrategyRouter(config, self.db, self.memory)
        self.scraper = ScrapingAgent(config, self.router, self.memory)
        self.extractor = ProductExtractionAgent(config, self.llm)
        self.news = NewsIntelligenceAgent(config, self.llm)
        self.events = EventDiscoveryAgent(config)
        self.normalizer = ProductNormalizationAgent(config, self.memory)
        self.trend_scorer = TrendScoringAgent(config)
        self.reasoner = TrendReasoningAgent(config, self.llm)
        self.validator = CredibilityValidationAgent(config)
        self.failure = FailureAgent(config, self.db, self.memory)
        self.recovery = SelfServeRecoveryAgent(config, self.db, self.scraper,
                                               self.router, self.discovery, self.memory)
        self.reddit_api = RedditAPIClient(config)
        self.exporter = Exporter(config, self.db)

        sc = config.get("scraping", {})
        self.max_pages = int(sc.get("max_pages_per_cycle", 120))
        self.max_pages_per_domain = int(sc.get("max_pages_per_domain", 20))
        self.max_depth = int(sc.get("max_crawl_depth", 3))
        # Recovery is expensive (alternative-source lookups). Cap it per cycle and
        # never run it for permanent per-page blocks (robots / 404), so a cycle
        # never stalls on many blocked pages.
        self.max_recovery_per_cycle = int(
            config.get("self_serve_recovery.max_recovery_per_cycle", 6))
        self._recovery_skip_types = {"blocked_by_robots", "http_404"}
        self.run_reference = config.run_reference
        self._cycle_stats: dict = {}

    # -- heartbeat ----------------------------------------------------------
    def _heartbeat(self, state: str, run_id: str, next_run_at: Optional[str] = None):
        s = self._cycle_stats
        self.db.insert("worker_heartbeat", {
            "timestamp": utc_iso(), "worker_status": "running",
            "current_loop_state": state, "current_run_id": run_id,
            "urls_discovered_this_cycle": s.get("urls_discovered", 0),
            "urls_crawled_this_cycle": s.get("urls_crawled", 0),
            "products_extracted_this_cycle": s.get("products_extracted", 0),
            "events_extracted_this_cycle": s.get("events_extracted", 0),
            "errors_this_cycle": s.get("errors", 0),
            "next_run_at": next_run_at,
        })

    def _baseline_mentions(self, product_id: str) -> float:
        row = self.db.query_one(
            "SELECT COUNT(*) c FROM product_sources WHERE product_id=?", (product_id,))
        return float(row["c"]) if row else 0.0

    # -- one full cycle -----------------------------------------------------
    def run_cycle(self) -> dict:
        run_id = f"{self.run_reference}-{uuid.uuid4().hex[:8]}"
        started = utc_iso()
        self._cycle_stats = {"urls_discovered": 0, "urls_crawled": 0, "urls_blocked": 0,
                             "products_extracted": 0, "products_validated": 0,
                             "events_extracted": 0, "errors": 0}
        self.db.insert("runs", {
            "run_id": run_id, "run_reference": self.run_reference, "started_at": started,
            "finished_at": None, "status": "running", "urls_discovered": 0,
            "urls_crawled": 0, "urls_blocked": 0, "products_extracted": 0,
            "products_validated": 0, "events_extracted": 0, "errors": 0,
        })
        log.info("=== CYCLE START run_id=%s ===", run_id)

        # 1. DISCOVER_SOURCES
        self._heartbeat("DISCOVER_SOURCES", run_id)
        discovered = self.discovery.discover()
        coverage = self.discovery.coverage(discovered)
        self._cycle_stats["urls_discovered"] = coverage["selected"]
        self._log_discovery(run_id, discovered)
        log.info("Discovered %d urls (%d selected); vertical coverage %.2f",
                 coverage["discovered"], coverage["selected"], coverage["vertical_coverage_score"])

        # LIGHT MODE: skip all page crawling. Products come purely from the feed/
        # API metadata (RSS/Shopify/Wikipedia/catalog titles + summaries + URL
        # slugs). No site hits => no 407/robots waits => fast cycles.
        light_mode = bool(self.config.get("runtime.light_mode", False))

        if light_mode:
            log.info("LIGHT MODE: skipping page crawl; using feed/API metadata only.")
            candidates, news_signals, event_records = [], [], []
            source_type_by_domain = {}
        else:
            # 2-4. VERIFY + SCORE + PRIORITIZE
            self._heartbeat("VERIFY_SOURCES", run_id)
            queue = self._build_queue(discovered)
            log.info("Crawl queue built: %d urls", len(queue))

            # 5-8. CRAWL + EXTRACT + NEWS + EVENTS (with bounded recursion)
            self._heartbeat("CRAWL_PAGES", run_id)
            candidates, news_signals, event_records, source_type_by_domain = \
                self._crawl_and_extract(run_id, queue)

        # Reddit official API (compliant) — process post text directly, no crawl
        if self.reddit_api.available():
            r_c, r_n, r_e = self._process_reddit_api(run_id)
            candidates += r_c
            news_signals += r_n
            event_records += r_e
            source_type_by_domain["reddit.com"] = "collector forum"

        # FEED-METADATA extraction (no crawl): every discovery entry contributes
        # products from its title/summary/URL slug — so 407/robots-blocked sites
        # still yield emerging-product signals into the master CSV.
        feed_c, feed_st = self._extract_from_feed_metadata(run_id, discovered)
        candidates += feed_c
        for _dom, _st in feed_st.items():
            source_type_by_domain.setdefault(_dom, _st)

        # seasonal events (calendar-driven)
        event_records += self.events.seasonal_events()
        self._cycle_stats["events_extracted"] = len(event_records)
        self._save_events(run_id, event_records)

        # 9. NORMALIZE
        self._heartbeat("NORMALIZE_PRODUCTS", run_id)
        products = self.normalizer.normalize(candidates, source_type_by_domain)

        # 10-11. SCORE + VALIDATE + REASON
        self._heartbeat("SCORE_TRENDS", run_id)
        rows = []
        news_catalyst_by_domain = self._news_catalyst_map(news_signals)
        for product in products:
            baseline = self._baseline_mentions(product.product_id)
            catalyst = max([news_catalyst_by_domain.get(d, 0.0) for d in product.source_domains] or [0.0])
            scored = self.trend_scorer.score(product, event_records, baseline, catalyst)
            scored["_cred_scores"] = [self.config.get("source_credibility_baselines", {}).get(st, 0.4)
                                      for st in product.source_types]
            reason = self.reasoner.reason(product, scored)
            validation = self.validator.validate(product, scored, reason)
            row = build_product_row(run_id, self.run_reference, product, scored, reason, validation)
            rows.append(row)
            self._persist_product(run_id, product, scored, reason, validation, source_type_by_domain)
            if validation in ("moderate", "strong"):
                self._cycle_stats["products_validated"] += 1
            # feed source quality memory with product yield
            for dom in product.source_domains:
                self.memory.record_result(dom, products=1,
                                          validated=1 if validation in ("moderate", "strong") else 0,
                                          eps=scored["EPS"], vtm=scored["VTM"],
                                          false_positive=(validation == "likely false positive"))
        self._cycle_stats["products_extracted"] = len(rows)

        # 12. SAVE
        self._heartbeat("SAVE_RESULTS", run_id)
        self._save_news(run_id, news_signals)
        self.exporter.save_products(rows, run_id)
        self.exporter.save_aux()
        self.memory.flush_residue(run_id)

        # finalize run
        self.db.execute(
            "UPDATE runs SET finished_at=?, status=?, urls_discovered=?, urls_crawled=?, "
            "urls_blocked=?, products_extracted=?, products_validated=?, events_extracted=?, errors=? "
            "WHERE run_id=?",
            (utc_iso(), "completed", self._cycle_stats["urls_discovered"],
             self._cycle_stats["urls_crawled"], self._cycle_stats["urls_blocked"],
             self._cycle_stats["products_extracted"], self._cycle_stats["products_validated"],
             self._cycle_stats["events_extracted"], self._cycle_stats["errors"], run_id))
        self.memory.decay_fatigue()
        self._heartbeat("REFRESH_DASHBOARD", run_id)
        log.info("=== CYCLE DONE run_id=%s products=%d validated=%d events=%d ===",
                 run_id, self._cycle_stats["products_extracted"],
                 self._cycle_stats["products_validated"], self._cycle_stats["events_extracted"])
        return {"run_id": run_id, **self._cycle_stats, "coverage": coverage}

    # -- helpers ------------------------------------------------------------
    def _log_discovery(self, run_id: str, discovered: list[DiscoveredURL]):
        rows = []
        for d in discovered:
            rows.append({
                "run_id": run_id, "discovered_at": utc_iso(),
                "discovery_method": d.discovery_method, "discovery_source": d.discovery_source[:300],
                "seed_query": d.seed_query[:200], "seed_url": d.seed_url[:300],
                "discovered_url": d.url[:600], "domain": d.domain,
                "source_type": d.source_type, "mapped_vertical": d.mapped_vertical,
                "mapped_category": d.mapped_category, "relevance_score": d.relevance_score,
                "credibility_score": d.credibility_score, "priority_score": d.priority_score,
                "selection_status": d.selection_status, "selection_reason": d.selection_reason[:300],
                "rejection_reason": d.rejection_reason[:300],
                "duplicate_flag": 1 if d.duplicate_flag else 0,
                "robots_status": d.robots_status, "crawl_allowed": 1 if d.crawl_allowed else 0,
                "extraction_status": "pending", "products_found": 0, "events_found": 0,
                "next_action": d.next_action,
            })
        self.db.insert_many("url_discovery_log", rows)

    def _build_queue(self, discovered: list[DiscoveredURL]) -> list[dict]:
        queue = []
        skipped_blocked = 0
        skip_blocked = self.config.get("scraping.skip_known_blocked_domains", True)
        for d in discovered:
            if d.selection_status != "selected":
                continue
            if self.failure.is_suppressed(d.url, d.domain):
                continue
            # don't re-hit a host that has only ever been blocked (407/403/robots);
            # feed-metadata extraction still mines its title/summary without crawling
            if skip_blocked and self.memory.is_blocked_domain(d.domain):
                skipped_blocked += 1
                continue
            verification = self.verifier.verify(d)
            if not verification.crawl_allowed:
                self._cycle_stats["urls_blocked"] += 1
                continue
            score = self.page_scorer.score(d, verification)
            mult = self.memory.domain_priority_multiplier(d.domain)
            final_priority = score.crawl_priority_score * mult
            queue.append({"url": d.url, "domain": d.domain, "vertical": d.mapped_vertical,
                          "source_type": verification.source_type,
                          "verification": verification, "score": score,
                          "priority": final_priority, "depth": d.depth})
        queue.sort(key=lambda x: x["priority"], reverse=True)
        if skipped_blocked:
            log.info("Skipped %d urls on known-blocked domains (feed metadata still used)",
                     skipped_blocked)
        return queue

    @staticmethod
    def _slug_words(url: str) -> str:
        """Turn a URL's last path segment into readable words — often the product.

        e.g. .../2026-panini-prizm-fifa-world-cup-soccer-cards -> that phrase.
        """
        try:
            seg = [s for s in urlparse(url).path.split("/") if s]
            if not seg:
                return ""
            last = re.sub(r"\.(html?|php|aspx?)$", "", seg[-1])
            words = re.sub(r"[-_]+", " ", last).strip()
            if len(words) < 4 or words.replace(" ", "").isdigit():
                return ""
            return words
        except Exception:
            return ""

    def _extract_from_feed_metadata(self, run_id: str, discovered: list[DiscoveredURL]):
        """No-crawl extraction: mine product candidates straight from discovery
        metadata (title + summary + URL slug). Every source (RSS, News, Wikipedia,
        Shopify, Reddit, GDELT, Trends, catalogs) contributes products even when
        the underlying page is blocked (407) or disallowed (robots) — no site hit.
        """
        if not self.config.get("extraction.enable_feed_extraction", True):
            return [], {}
        candidates = []
        source_type_by_domain: dict[str, str] = {}
        seen_urls: set[str] = set()
        used = 0
        for d in discovered:
            if d.selection_status != "selected" or d.url in seen_urls:
                continue
            seen_urls.add(d.url)
            title = (d.title or "").strip()
            snippet = (d.snippet or "").strip()
            slug = self._slug_words(d.url)
            parts = [p for p in (title, snippet, slug) if p]
            if not parts:
                continue
            text = ". ".join(parts)
            if len(text) < 8:
                continue
            page = FetchedPage(
                url=d.url, domain=d.domain, status="ok", http_status=0,
                title=title or slug, text=text[:2000],
                published_at=d.published_at, extraction_method="feed_metadata",
                meta={"discovery_method": d.discovery_method, "source_type": d.source_type},
                fetched_at=utc_iso(), text_hash=sha1(text[:1000]),
            )
            pcs = self.extractor.extract(page)
            if pcs:
                candidates.extend(pcs)
                if d.domain:
                    source_type_by_domain.setdefault(d.domain, d.source_type or "unknown")
            used += 1
        log.info("Feed-metadata extraction: %d candidates from %d discovery entries (no crawl)",
                 len(candidates), used)
        return candidates, source_type_by_domain

    def _crawl_and_extract(self, run_id: str, queue: list[dict]):
        candidates = []
        news_signals = []
        event_records = []
        source_type_by_domain: dict[str, str] = {}
        visited: set[str] = set()
        per_domain: dict[str, int] = {}
        blocked_this_cycle: set[str] = set()   # hosts that 407/403/429'd — skip their rest
        pages_done = 0
        recovery_used = 0
        skipped_host_blocked = 0
        # use a mutable list as a growable queue for recursion
        work = list(queue)
        idx = 0
        while idx < len(work) and pages_done < self.max_pages:
            item = work[idx]
            idx += 1
            url = item["url"]
            domain = item["domain"]
            if url in visited:
                continue
            if domain in blocked_this_cycle:
                skipped_host_blocked += 1
                continue
            if per_domain.get(domain, 0) >= self.max_pages_per_domain:
                continue
            visited.add(url)
            per_domain[domain] = per_domain.get(domain, 0) + 1
            source_type_by_domain[domain] = item["source_type"]

            page = self.scraper.fetch(url, item["source_type"])
            pages_done += 1
            self._cycle_stats["urls_crawled"] = pages_done
            self._record_crawl_url(run_id, item, page)

            if page.status in ("ok",) and page.text:
                # extraction
                page_candidates = self.extractor.extract(page)
                candidates.extend(page_candidates)
                if not page_candidates:
                    self.memory.record_output("ProductExtractionAgent", useless=True)
                else:
                    self.memory.record_output("ProductExtractionAgent", useless=False)
                # news mapping
                ns = self.news.analyze(page, item["verification"].source_credibility_score)
                if ns:
                    news_signals.append(ns)
                # event discovery
                ev = self.events.from_page(page)
                if ev:
                    event_records.append(ev)
                self._record_page(run_id, item, page, len(page_candidates))
                # recursive expansion
                if item["depth"] < self.max_depth and pages_done < self.max_pages:
                    expanded = self.discovery.expand_links(
                        page.links[:40], url, item["vertical"], item["depth"] + 1)
                    for du in expanded:
                        if du.url in visited:
                            continue
                        verification = self.verifier.verify(du)
                        if not verification.crawl_allowed:
                            continue
                        score = self.page_scorer.score(du, verification)
                        work.append({"url": du.url, "domain": du.domain,
                                     "vertical": du.mapped_vertical,
                                     "source_type": verification.source_type,
                                     "verification": verification, "score": score,
                                     "priority": score.crawl_priority_score, "depth": du.depth})
            else:
                # failure path → recovery ladder before decay
                ftype = classify_failure(page)
                # host-level blocks (proxy 407, 403, rate-limit 429) apply to the
                # whole domain — skip its remaining queued urls this cycle
                if ftype in ("http_407", "http_403", "http_429"):
                    blocked_this_cycle.add(domain)
                if page.status in ("blocked", "robots_blocked"):
                    self._cycle_stats["urls_blocked"] += 1
                self._cycle_stats["errors"] += 1
                self.memory.record_output("ScrapingAgent", useless=True, success=False)
                self.failure.record(run_id, "ScrapingAgent", url, domain, ftype,
                                    page.error_message)
                # Only run the (expensive) recovery ladder for fixable failures,
                # within a per-cycle budget. Skip permanent per-page blocks so the
                # cycle never stalls on many robots-blocked/404 pages.
                if ftype not in self._recovery_skip_types and recovery_used < self.max_recovery_per_cycle:
                    recovery_used += 1
                    rec = self.recovery.recover(run_id, url, ftype, item["source_type"],
                                                page.extraction_method)
                    if rec.get("result") == "recovered_by_strategy":
                        page2 = self.scraper.fetch(url, item["source_type"])
                        if page2.status == "ok":
                            candidates.extend(self.extractor.extract(page2))
            if pages_done % 10 == 0:
                self._heartbeat("CRAWL_PAGES", run_id)
        log.info("Crawled %d pages, extracted %d candidates, %d news, %d events"
                 " (skipped %d urls on hosts blocked mid-cycle)",
                 pages_done, len(candidates), len(news_signals), len(event_records),
                 skipped_host_blocked)
        return candidates, news_signals, event_records, source_type_by_domain

    def _process_reddit_api(self, run_id: str):
        """Fetch Reddit posts via the official API and run them through
        extraction / news / events directly (no crawling of robots-blocked pages)."""
        candidates, news_signals, events = [], [], []
        try:
            pages = self.reddit_api.fetch_pages()
        except Exception as exc:
            log.warning("Reddit API step failed: %s", exc)
            return candidates, news_signals, events
        cred = self.config.get("source_credibility_baselines", {}).get("collector forum", 0.65)
        for page in pages:
            try:
                pc = self.extractor.extract(page)
                candidates.extend(pc)
                ns = self.news.analyze(page, cred)
                if ns:
                    news_signals.append(ns)
                ev = self.events.from_page(page)
                if ev:
                    events.append(ev)
                self.db.insert("crawl_urls", {
                    "run_id": run_id, "url": page.url, "domain": "reddit.com",
                    "source_type": "collector forum", "discovered_at": utc_iso(),
                    "last_crawled_at": utc_iso(), "crawl_status": "ok (reddit_api)",
                    "crawl_priority_score": 0.6, "page_relevance_score": 0.6,
                    "source_credibility_score": cred,
                    "product_signal_score": 0.5 if pc else 0.0, "error_message": "",
                })
            except Exception as exc:
                log.debug("Reddit page process error: %s", exc)
        self._cycle_stats["urls_crawled"] = self._cycle_stats.get("urls_crawled", 0) + len(pages)
        log.info("Reddit API: %d posts -> %d candidates, %d news, %d events",
                 len(pages), len(candidates), len(news_signals), len(events))
        return candidates, news_signals, events

    def _news_catalyst_map(self, news_signals) -> dict:
        m: dict[str, float] = {}
        for ns in news_signals:
            m[ns.source_domain] = max(m.get(ns.source_domain, 0.0), ns.news_signal_score)
        return m

    # -- persistence --------------------------------------------------------
    def _record_crawl_url(self, run_id, item, page):
        self.db.insert("crawl_urls", {
            "run_id": run_id, "url": item["url"], "domain": item["domain"],
            "source_type": item["source_type"], "discovered_at": utc_iso(),
            "last_crawled_at": utc_iso(), "crawl_status": page.status,
            "crawl_priority_score": item["priority"],
            "page_relevance_score": item["score"].page_relevance_score,
            "source_credibility_score": item["score"].source_credibility_score,
            "product_signal_score": item["score"].product_signal_score,
            "error_message": page.error_message,
        })
        # source_domains rolling counters
        self.db.upsert("source_domains", {
            "domain": item["domain"], "source_type": item["source_type"],
            "credibility_score": item["score"].source_credibility_score,
            "pages_seen": 1, "products_found": 0,
            "blocked_count": 1 if page.status in ("blocked", "robots_blocked") else 0,
            "last_seen_at": utc_iso(), "notes": page.status,
        }, ["domain"])

    def _record_page(self, run_id, item, page, product_count):
        self.db.insert("pages", {
            "run_id": run_id, "url": page.url, "domain": page.domain,
            "title": page.title[:300], "published_at": page.published_at,
            "crawled_at": utc_iso(), "text_hash": page.text_hash,
            "extraction_method": page.extraction_method,
            "page_relevance_score": item["score"].page_relevance_score,
            "source_credibility_score": item["score"].source_credibility_score,
            "product_signal_score": item["score"].product_signal_score,
            "status": page.status,
        })

    def _persist_product(self, run_id, product, scored, reason, validation, stype_by_domain):
        self.db.insert("products", {
            "run_id": run_id, "product_id": product.product_id,
            "product_name": product.rep.product_name,
            "canonical_product_name": product.canonical_product_name,
            "vertical": product.rep.vertical, "category": product.rep.category,
            "subcategory": product.rep.subcategory, "brand": product.rep.brand,
            "eps": scored["EPS"], "vtm": scored["VTM"],
            "final_trend_status": scored["final_trend_status"],
            "validation_status": validation, "confidence_level": reason.confidence_level,
            "ai_trend_reason": reason.ai_trend_reason,
            "evidence_summary": reason.evidence_summary,
            "first_seen_at": product.first_seen, "last_seen_at": product.last_seen,
        })
        for c in product.candidates:
            self.db.insert("product_sources", {
                "product_id": product.product_id, "run_id": run_id,
                "url": c.source_url, "domain": c.source_domain,
                "source_type": stype_by_domain.get(c.source_domain, "unknown"),
                "source_credibility_score": self.config.get(
                    "source_credibility_baselines", {}).get(
                    stype_by_domain.get(c.source_domain, "unknown"), 0.4),
                "evidence_snippet": c.evidence_snippet[:400],
                "observed_at": c.observed_at_utc,
            })

    def _save_news(self, run_id, news_signals):
        for ns in news_signals:
            self.db.insert("news_signals", {
                "run_id": run_id, "news_url": ns.news_url, "news_title": ns.news_title[:300],
                "source_domain": ns.source_domain, "entity_detected": ns.entity_detected,
                "mapped_product_id": "", "mapped_product_name": ", ".join(ns.mapped_product_candidates[:3]),
                "news_signal_score": ns.news_signal_score,
                "news_to_product_reason": ns.news_to_product_reason[:400],
                "observed_at": ns.observed_at,
            })

    def _save_events(self, run_id, events):
        for ev in events:
            self.db.upsert("event_calendar", {
                "event_id": ev.event_id, "run_id": run_id, "event_name": ev.event_name[:300],
                "event_type": ev.event_type, "event_date": ev.event_date,
                "event_start_date": ev.event_start_date, "event_end_date": ev.event_end_date,
                "event_status": ev.event_status, "days_until_event": ev.days_until_event,
                "days_since_event": ev.days_since_event,
                "affected_verticals": ", ".join(ev.affected_verticals),
                "affected_categories": ", ".join(ev.affected_categories),
                "mapped_products": ", ".join(ev.mapped_products),
                "event_source_url": ev.event_source_url, "event_source_domain": ev.event_source_domain,
                "event_confidence_score": ev.event_confidence_score,
                "seasonality_score": ev.seasonality_score,
                "expected_product_impact_reason": ev.expected_product_impact_reason[:400],
                "created_at": utc_iso(), "updated_at": utc_iso(),
            }, ["event_id"])

    def close(self):
        try:
            self.scraper.close()
            self.discovery.close()
            self.recovery.tool_agent.close()
            self.reddit_api.close()
        except Exception:
            pass
