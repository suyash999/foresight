# External Emerging Collectibles Intelligence Agent

An autonomous, agentic, **high-recall** product-trend discovery system that
continuously scans **external public sources** (news, manufacturer pages,
auction houses, hobby publications, release calendars, forums, marketplaces,
RSS, GDELT, Wikipedia/Wikidata, sitemaps) to identify collectible products that
may be **emerging, spiking, or likely to see demand growth** — across every
collectibles vertical.

It discovers URLs, verifies source credibility, scores pages, crawls
responsibly, extracts product candidates, maps real-world events to products,
scores trends with two transparent metrics (**EPS** and **VTM**), validates
across independent sources, generates evidence-backed AI reasoning, persists to
SQLite / CSV / Parquet / JSON, and serves a **live Streamlit dashboard** — all
on a continuous background loop.

> **eBay is excluded by default.** The system is *external-only* intelligence:
> it discovers external market signals and never depends on eBay internal data.
> The exclusion is configurable.

---

## What the system does

- **Discovers broadly** (high recall) from many channels: RSS feeds, sitemaps,
  GDELT global news, Wikipedia, Wikidata, compliant search APIs (Google CSE /
  Bing / SerpAPI when configured), seed/official/retail/marketing pages, and
  **recursive expansion** from credible crawled pages.
- **Verifies** each source (type, credibility, SEO/spam risk, eBay/exclusion).
- **Scores** each page (relevance, freshness, product/news signal → priority).
- **Crawls** responsibly (robots.txt, rate limits, 429/403 handling, backoff)
  using an **adaptive strategy router** (HTTP+parsers first; Playwright/Camoufox
  only when enabled/needed).
- **Extracts** structured product candidates (JSON-LD, article text, tables,
  headings, line-scan) with vertical-specific fields (cards / coins / comics /
  toys / watches / vinyl).
- **Maps news & events** to products (a rookie record → rookie cards; a set
  sells out → sealed products; a watch is discontinued → resale interest).
- **Links each product to a real-world event** (never hallucinated) with dates,
  status (upcoming/live/recent/historical), proximity and seasonality scores.
- **Normalizes & deduplicates** products across sources (canonical key + fuzzy).
- **Scores trends** with **EPS** (emerging) and **VTM** (validated momentum),
  assigns a **final trend status**, and explains every score.
- **Validates** trends and assigns a validation status.
- **Learns over time**: source-quality memory, decay, fatigue, recovery boost,
  agent residue, strategy registry, tool discovery, and a self-serve recovery
  ladder — all persisted so future runs improve.
- **Serves a live dashboard** (eBay-inspired colors) with URL sourcing
  intelligence, funnels, source mix, products, events, agent health, failures,
  and self-serve learning — auto-refreshing while the backend keeps running.

---

## Architecture (text diagram)

```
                         ┌───────────────────────────────────────────────┐
                         │              ORCHESTRATION AGENT               │
                         │  DISCOVER → VERIFY → SCORE_PAGES → PRIORITIZE   │
                         │  → CRAWL → EXTRACT → MAP_NEWS → DISCOVER_EVENTS │
                         │  → NORMALIZE → SCORE_TRENDS → VALIDATE          │
                         │  → GENERATE_REASONS → SAVE → REFRESH → SLEEP →⟳ │
                         └───────────────────────────────────────────────┘
   Discovery channels            Agents                       Persistence
 ┌──────────────────┐   ┌──────────────────────────┐   ┌────────────────────┐
 │ RSS  Sitemaps    │   │ SourceDiscoveryAgent      │   │ SQLite (23 tables) │
 │ GDELT Wikipedia  │──▶│ SourceVerificationAgent   │──▶│ products_latest.*  │
 │ Wikidata Search  │   │ PageScoringAgent          │   │  (csv/parquet/json)│
 │ Seeds Retail     │   │ ScrapingAgent + Router    │   │ source_scores.csv  │
 │ Recursive links  │   │ ProductExtractionAgent    │   │ url_crawl_log.csv  │
 └──────────────────┘   │ NewsIntelligenceAgent     │   │ news_signal.csv    │
                        │ EventDiscoveryAgent        │   └────────────────────┘
 Self-learning memory   │ ProductNormalizationAgent  │            │
 ┌──────────────────┐   │ TrendScoringAgent (EPS/VTM)│            ▼
 │ source_quality   │◀─▶│ TrendReasoningAgent        │   ┌────────────────────┐
 │ strategy_registry│   │ CredibilityValidationAgent │   │ LIVE STREAMLIT      │
 │ agent_failures   │   │ FailureAgent (self-heal)   │   │ DASHBOARD           │
 │ recovery_attempts│   │ SelfServeRecoveryAgent     │──▶│ (reads only,        │
 │ tool_candidates  │   │  ├ AdaptiveStrategyRouter  │   │  auto-refresh)      │
 │ domain_learning  │   │  └ OpenSourceToolDiscovery │   └────────────────────┘
 └──────────────────┘   └──────────────────────────┘
```

### Agents

| Agent | Purpose |
|---|---|
| **SourceDiscoveryAgent** | High-recall multi-channel URL discovery + recursive expansion; recovery mode when too few URLs found. |
| **SourceVerificationAgent** | Trust/block/deprioritize; classify source type; eBay exclusion; credibility + SEO/spam risk. |
| **PageScoringAgent** | Relevance, freshness, product/news signal → crawl priority. |
| **ScrapingAgent** | Fetch via adaptive strategy ladder; robots/rate-limit compliant; classify failures. |
| **AdaptiveStrategyRouter** | Chooses/learns best extraction strategy per domain from the strategy registry. |
| **ProductExtractionAgent** | Structured product candidates (JSON-LD, article, tables, line-scan) + vertical fields. |
| **NewsIntelligenceAgent** | Detect events/entities in news; map to product candidates. |
| **EventDiscoveryAgent** | Discover past/live/upcoming events + seasonality; proximity scoring; no hallucinated dates. |
| **ProductNormalizationAgent** | Canonical + fuzzy dedupe; stable product IDs; merge sources/evidence. |
| **TrendScoringAgent** | EPS + VTM (transparent, config-weighted), event linkage, final status. |
| **TrendReasoningAgent** | Evidence-backed AI reason, key signals, counter-signals, next validation. |
| **CredibilityValidationAgent** | Validation status from independent credible coverage + evidence. |
| **FailureAgent** | Records failures, controlled recovery actions, decay/backoff, suppression. |
| **SelfServeRecoveryAgent** | Recovery ladder → alternate strategies → alternative public sources → tool discovery → sandbox test. |
| **OpenSourceToolDiscoveryAgent** | Finds & scores PyPI/GitHub tools before use (recommend-only by default). |
| **Orchestrator + Scheduler** | Runs the continuous loop with heartbeats, checkpointing, graceful shutdown. |

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: browser rendering (disabled by default)
pip install "camoufox[geoip]" playwright
python3 -m camoufox fetch          # Windows: python -m camoufox fetch
python -m playwright install chromium

# Optional: copy env template and add keys (all optional)
cp .env.example .env
```

> **Note on `feedparser`:** its transitive dep `sgmllib3k` ships a broken sdist
> on modern setuptools. If `pip install -r requirements.txt` fails on it, run:
> `pip install --no-deps feedparser` and ensure `sgmllib.py` is importable (the
> Dockerfile handles this automatically).

---

## Run commands

```bash
# One agentic cycle
python -m emerging_collectibles_agent.main run-once --config config.yaml

# Continuous background worker (keeps crawling on an interval)
python -m emerging_collectibles_agent.main run-forever --config config.yaml

# Live dashboard (reads outputs; auto-refreshes)
streamlit run src/emerging_collectibles_agent/dashboard/streamlit_app.py

# Worker + dashboard together (one command)
python -m emerging_collectibles_agent.main run-app --config config.yaml

# Export the latest dataframe from the database
python -m emerging_collectibles_agent.main export --config config.yaml

# Tests
pytest tests/

# Docker (worker + dashboard)
docker compose up
```

The dashboard runs at <http://localhost:8501>. The **backend worker runs
independently** — the dashboard only reads SQLite/Parquet and auto-refreshes, so
crawling continues even when the dashboard is closed. The dashboard shows
whether the worker is **LIVE** or **STALE** via heartbeats.

---

## Dashboard

Sections: **Executive overview**, **Emerging products** (high-recall table +
per-product detail with EPS/VTM breakdowns, evidence, sources), **URL Sourcing
Intelligence** (every discovered URL with why it was selected/rejected), **URL
funnel** and **source mix**, **Crawl monitor**, **Source credibility &
learning**, **News catalysts**, **Events & seasonality** (with event-linked
products), **Trend timeline** charts, **Agent health / failures / recovery**,
and **Self-serve learning** (strategy registry, recovery attempts, tool
discovery). Filters: vertical, category, source type, event linkage, min EPS,
min VTM, confidence, validation, trend status.

Colors: Red `#E53238`, Blue `#0064D2`, Yellow `#F5AF02`, Green `#86B817`, dark
text `#111827`, light background `#F9FAFB`.

---

## Scoring

### Emerging Product Score (EPS, 0–100)

```
EPS = 100 * [ 0.17*Freshness + 0.13*SourceCredibility + 0.14*MentionVelocity
            + 0.12*SourceDiversity + 0.12*NewsCatalyst + 0.10*EventProximity
            + 0.08*MarketplaceSignal + 0.06*ScarcitySignal + 0.05*ReleaseSignal
            + 0.03*AIConfidence ]
```

- `Freshness = exp(-hours_since_latest / half_life)` (news 48h, cards 72h,
  watches/coins/vinyl/comics 168h)
- `MentionVelocity = min(1, recent_mentions / (baseline_mentions + smoothing))`
- `SourceDiversity = min(1, unique_source_types / target)` (target 4)
- `EventProximity = exp(-|days_to_or_since_event| / event_half_life_days)`

Interpretation: 80–100 strong · 60–79 moderate · 40–59 watchlist · 20–39 low ·
0–19 not emerging.

### Validated Trend Momentum (VTM, 0–100)

```
VTM = 100 * [ 0.22*CredibleSourceCoverage + 0.18*IndependentConfirmation
            + 0.14*MentionAcceleration + 0.14*EvidenceQuality + 0.12*EventValidation
            + 0.08*CrossCategoryCatalyst + 0.07*RecencyConsistency
            + 0.05*NoisePenaltyAdjusted ]
```

- `CredibleSourceCoverage = min(1, credible_source_count / required)` (req 3)
- `IndependentConfirmation = independent_domain_count / total_domain_count`
- `MentionAcceleration = clamp((recent_rate - baseline_rate) / max(baseline_rate, smoothing), 0, 1)`
- `NoisePenaltyAdjusted` starts at 1.0 minus spam/duplicate/single-source/old/vague penalties.

Interpretation: 80–100 strong · 60–79 developing · 40–59 early · 20–39 weak ·
0–19 not validated.

### Final trend status (EPS + VTM)

`Strong Emerging` (EPS≥75, VTM≥65) · `Validated Momentum` (EPS≥60, VTM≥75) ·
`Emerging but Needs Validation` (EPS≥70, VTM<65) · `Potential False Positive`
(EPS≥70, VTM<35) · `Watchlist` · `Low Confidence`. All weights and thresholds
are in `config.yaml`.

### Event proximity half-lives

sports/news 14d · product release/drop 30d · movie/show/comic 45d ·
auction/market 21d · watches/coins/vinyl long-cycle 90d.

---

## Responsible scraping rules

- Public pages only; respects `robots.txt` (when enabled) and crawl-delay.
- Per-domain rate limits + exponential backoff; honours HTTP **429** (blocks &
  backs off) and **401/403** (marks blocked — never bypassed).
- **Never** bypasses logins/paywalls, solves CAPTCHAs, evades explicit anti-bot
  blocks, scrapes private/personal data, or overloads sites.
- Blocked pages are marked blocked and skipped; the recovery ladder looks for
  **alternative public sources** discussing the same product/event instead of
  forcing a blocked source.
- All crawl decisions are logged (transparent), and browser rendering is used
  only for legitimate public JavaScript pages when explicitly enabled.
- Search: no direct scraping of Google SERPs — only compliant search APIs.

---

## External-only source behavior

`external_only_mode: true` and `excluded_domains` include all eBay domains by
default. To include eBay (not recommended for external intelligence), remove the
eBay entries from `excluded_domains` and/or set `external_only_mode: false`. Use
`allowed_domains` to restrict to a whitelist. The system is **global by
default** (`geography.mode: global`) and supports country/region/language modes.

---

## Configuration — how to extend (config only, no code)

- **Add a vertical:** add it under `verticals:` (and to `vertical_coverage`).
  Add category hints under `taxonomy.categories:` and queries under
  `seed_search_queries:`.
- **Add a sport / franchise:** add to `taxonomy.sports:` / `taxonomy.franchises:`.
- **Add source URLs / feeds:** add to `seed_rss_feeds:`, `seed_sitemaps:`,
  `seed_urls:`, or `known_sources:` (with credibility hints).
- **Tune scoring:** edit `eps_weights`, `vtm_weights`, `scoring_params`,
  `source_credibility_baselines`, `validation_thresholds`,
  `event_intelligence.event_half_life_days`.
- **Tune discovery recall:** `url_discovery` budgets
  (`min/target/max_urls_per_cycle`, `max_recursion_depth`, channel toggles,
  `relevance_threshold`).
- **Colors:** `dashboard.colors`.

### Camoufox / Playwright

Disabled by default (`scraping.use_camoufox: false`, `use_playwright: false`).
Enable either to allow JS rendering for legitimate public pages. Install per the
Setup section. The adaptive router only escalates to a browser strategy when it
is enabled.

### LLM (optional)

Deterministic by default (`llm.use_llm: false`) — **the system never crashes
without an LLM**. To enable, set `llm.use_llm: true` and provide `LLM_API_KEY`
(any OpenAI-compatible endpoint via `LLM_BASE_URL`, `LLM_MODEL`). The LLM is
used only for messy extraction, trend reasoning, news→product mapping, and
credibility explanation. All LLM outputs are structured JSON validated with
Pydantic; on failure the system retries once, attempts a safe repair, then falls
back to deterministic logic.

---

## Self-serve learning, tool discovery, sandbox installs

When a source fails, the **SelfServeRecoveryAgent** does **not** immediately
decay it. It walks a compliant recovery ladder: retry/backoff → alternate parser
→ article/metadata/JSON-LD → RSS/sitemap → browser render (if enabled) →
**alternative public sources** → tool discovery → sandbox test → promote/rollback
→ (only then) decay. It records failures, recovery attempts, and per-domain
strategy performance so future runs pick better strategies.

The **OpenSourceToolDiscoveryAgent** finds and **scores** PyPI/GitHub tools
before any use:

```
ToolCandidateScore = 0.20*maintenance + 0.20*security + 0.15*compatibility
                   + 0.15*license + 0.10*documentation + 0.10*popularity
                   + 0.05*test_coverage + 0.05*usefulness
```

**Safe installation** is controlled by `self_serve_recovery`:
`auto_install_tools: false` and `tool_install_mode: recommend_only` by default —
nothing is installed. Modes: `recommend_only` (recommend, install nothing),
`sandbox_auto_install` (install **only** into an isolated venv under `.plugins/`
and test before promotion), `approved_auto_install` (allowlist only, still
sandbox-tested). **Arbitrary GitHub code is never installed into the main
runtime.** Every install writes a manifest to `.plugins/manifests/`.

The system learns via **operational memory** (source quality, strategy registry,
domain learning, prompt/rule performance, failure decay, recovery boost) — this
is *not* model retraining, and the code makes no such claim.

---

## Optional Kryo / Cloud Code API adapter — governance notes

`src/emerging_collectibles_agent/cloud_code_adapter.py` provides a clean,
**disabled-by-default** interface (`cloud_code.use_cloud_code_api: false`).
Governance assumptions and risks:

- **No internal data leaves the process** unless *both*
  `use_cloud_code_api: true` **and** `send_internal_data: true` are set — a
  deliberate two-flag opt-in for governance review.
- **No secrets are hardcoded** — keys come from `.env` via the env-var names in
  config. If keys are absent, the adapter disables itself.
- If Cloud Code / Kryo is unavailable or errors, **the system still runs fully
  locally** (the adapter is a no-op).
- **Risk:** enabling external enrichment sends payloads to a third party; review
  data-handling, retention, and compliance before enabling.

---

## Database tables (SQLite)

Operational: `runs`, `crawl_urls`, `pages`, `products`, `product_sources`,
`news_signals`, `source_domains`, `product_dataframe`, `url_discovery_log`,
`event_calendar`, `worker_heartbeat`.
Self-learning: `agent_failures`, `agent_residue`, `source_quality_memory`,
`extraction_rule_performance`, `prompt_performance`, `event_mapping_performance`,
`false_positive_memory`, `crawl_priority_memory`, `scraping_failures`,
`recovery_attempts`, `strategy_registry`, `tool_candidates`, `installed_tools`,
`domain_learning_memory`.

Outputs: `outputs/products_latest.{csv,parquet,json}`,
`source_scores_latest.csv`, `url_crawl_log_latest.csv`, `news_signal_latest.csv`.

---

## Known limitations

- **Recall depends on reachable sources.** Behind a restrictive network/proxy,
  discovery channels (GDELT/Wikipedia/search APIs) may be blocked; the system
  fast-fails (circuit breaker + discovery time budget) and uses whatever feeds
  are reachable rather than hanging. Provide search-API keys and open network
  access for full high-recall discovery.
- **High recall means noise.** By design low-confidence products are *kept and
  labeled*, not dropped — use dashboard filters (EPS/VTM/confidence/validation).
  Text-scan candidates can include long phrases; scoring/confidence de-emphasise
  them.
- **Deterministic extraction is heuristic.** Enable the LLM for messy pages.
- **Event dates are only shown when found in a source** — never hallucinated;
  unknown dates are marked as such.
- **Tool auto-install is off by default** and, when enabled, is sandbox-only.

## Future improvements

- Embedding-based product dedupe and semantic near-duplicate URL detection.
- Async/concurrent crawling with a shared token-bucket per domain.
- Pluggable search providers and per-vertical extraction plugins.
- Price-history ingestion from public marketplaces for stronger MarketplaceSignal.
- Promotion of sandbox-tested tools into a versioned plugin runtime.
- Alerting (email/Slack) on Strong Emerging + Validated Momentum products.

---

## License

MIT.
