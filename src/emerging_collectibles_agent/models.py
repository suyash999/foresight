"""Pydantic models for all structured data flowing through the pipeline.

These are the contracts between agents. LLM outputs are validated against the
relevant models; deterministic agents produce the same shapes.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# URL discovery / sourcing
# ---------------------------------------------------------------------------
class DiscoveredURL(BaseModel):
    url: str
    domain: str = ""
    discovery_method: str = "unknown"   # rss | sitemap | gdelt | wikipedia | wikidata | search_api | recursive | seed | news
    discovery_source: str = ""          # feed url / query / parent url
    seed_query: str = ""
    seed_url: str = ""
    source_type: str = "unknown"
    mapped_vertical: str = ""
    mapped_category: str = ""
    title: str = ""
    snippet: str = ""
    published_at: Optional[str] = None
    depth: int = 0
    relevance_score: float = 0.0
    credibility_score: float = 0.0
    priority_score: float = 0.0
    selection_status: str = "pending"   # selected | rejected | pending | duplicate
    selection_reason: str = ""
    rejection_reason: str = ""
    duplicate_flag: bool = False
    robots_status: str = "unknown"      # allowed | blocked | unknown
    crawl_allowed: bool = True
    next_action: str = "verify"


# ---------------------------------------------------------------------------
# Source verification
# ---------------------------------------------------------------------------
class SourceVerification(BaseModel):
    source_url: str
    domain: str
    source_type: str = "unknown"
    source_credibility_score: float = 0.4
    verification_status: str = "unknown"   # trusted | ignore | blocked | deprioritized
    verification_reason: str = ""
    crawl_allowed: bool = True
    exclusion_reason: str = ""
    seo_spam_risk: float = 0.0


# ---------------------------------------------------------------------------
# Page scoring
# ---------------------------------------------------------------------------
class PageScore(BaseModel):
    url: str
    page_relevance_score: float = 0.0
    source_credibility_score: float = 0.0
    freshness_score: float = 0.0
    product_signal_score: float = 0.0
    news_signal_score: float = 0.0
    crawl_priority_score: float = 0.0
    scoring_reason: str = ""


# ---------------------------------------------------------------------------
# Fetched page
# ---------------------------------------------------------------------------
class FetchedPage(BaseModel):
    url: str
    domain: str = ""
    status: str = "pending"      # ok | blocked | error | robots_blocked
    http_status: Optional[int] = None
    title: str = ""
    text: str = ""
    html: str = ""
    published_at: Optional[str] = None
    extraction_method: str = ""  # httpx+trafilatura | jsonld | readability | playwright | camoufox ...
    links: list[str] = Field(default_factory=list)
    jsonld: list[dict] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    error_message: str = ""
    fetched_at: Optional[str] = None
    runtime_seconds: float = 0.0
    text_hash: str = ""


# ---------------------------------------------------------------------------
# Product extraction candidate
# ---------------------------------------------------------------------------
class ProductCandidate(BaseModel):
    product_name: str
    canonical_product_name: str = ""
    vertical: str = ""
    category: str = ""
    subcategory: str = ""
    brand: str = ""
    franchise: str = ""
    sport: str = ""
    team: str = ""
    player_or_character: str = ""
    product_line: str = ""
    product_type: str = ""
    set_name: str = ""
    release_date: str = ""
    release_year: str = ""
    price: Optional[float] = None
    currency: str = ""
    scarcity_language: str = ""
    demand_language: str = ""
    auction_or_sales_signal: str = ""
    news_signal: str = ""
    evidence_snippet: str = ""
    source_url: str = ""
    source_domain: str = ""
    extraction_method: str = ""
    extraction_confidence: float = 0.0
    observed_at_utc: str = ""
    observed_at_ist: str = ""

    # geography
    detected_country: str = ""
    detected_region: str = ""
    source_country: str = ""
    market_scope: str = "global"
    global_signal_flag: bool = True
    local_signal_flag: bool = False
    geo_confidence: float = 0.5

    # trading-card specific
    card_set: str = ""
    card_number: str = ""
    rookie_card_flag: bool = False
    autograph_flag: bool = False
    memorabilia_flag: bool = False
    parallel_variant: str = ""
    serial_numbered_flag: bool = False
    print_run: str = ""
    grading_company: str = ""
    grade: str = ""
    sealed_product_type: str = ""
    hobby_box_flag: bool = False
    blaster_box_flag: bool = False
    booster_box_flag: bool = False
    case_flag: bool = False

    # other collectibles
    coin_year: str = ""
    coin_mint: str = ""
    coin_grade: str = ""
    comic_issue_number: str = ""
    comic_publisher: str = ""
    toy_brand: str = ""
    watch_model: str = ""
    watch_reference_number: str = ""
    vinyl_artist: str = ""
    vinyl_album: str = ""
    limited_edition_flag: bool = False


# ---------------------------------------------------------------------------
# News signal
# ---------------------------------------------------------------------------
class NewsSignal(BaseModel):
    news_url: str
    news_title: str = ""
    source_domain: str = ""
    entity_detected: str = ""
    mapped_product_candidates: list[str] = Field(default_factory=list)
    news_signal_score: float = 0.0
    news_to_product_reason: str = ""
    source_credibility_score: float = 0.0
    event_type: str = ""
    observed_at: str = ""


# ---------------------------------------------------------------------------
# Event / seasonality
# ---------------------------------------------------------------------------
class EventRecord(BaseModel):
    event_id: str = ""
    event_name: str = ""
    event_type: str = ""
    event_date: str = ""
    event_start_date: str = ""
    event_end_date: str = ""
    event_status: str = "unknown"   # upcoming | live | recently_happened | historical | not_found
    days_until_event: Optional[int] = None
    days_since_event: Optional[int] = None
    affected_verticals: list[str] = Field(default_factory=list)
    affected_categories: list[str] = Field(default_factory=list)
    mapped_products: list[str] = Field(default_factory=list)
    mapped_brands: list[str] = Field(default_factory=list)
    mapped_people: list[str] = Field(default_factory=list)
    mapped_franchises: list[str] = Field(default_factory=list)
    event_source_url: str = ""
    event_source_domain: str = ""
    event_confidence_score: float = 0.0
    seasonality_score: float = 0.0
    expected_product_impact_reason: str = ""


# ---------------------------------------------------------------------------
# Scoring breakdowns
# ---------------------------------------------------------------------------
class EPSBreakdown(BaseModel):
    freshness_score: float = 0.0
    source_credibility_score: float = 0.0
    mention_velocity_score: float = 0.0
    source_diversity_score: float = 0.0
    news_catalyst_score: float = 0.0
    event_proximity_score: float = 0.0
    marketplace_signal_score: float = 0.0
    scarcity_signal_score: float = 0.0
    release_signal_score: float = 0.0
    ai_confidence_score: float = 0.0
    EPS: float = 0.0


class VTMBreakdown(BaseModel):
    credible_source_coverage: float = 0.0
    independent_confirmation: float = 0.0
    mention_acceleration: float = 0.0
    evidence_quality: float = 0.0
    event_validation_score: float = 0.0
    cross_category_catalyst: float = 0.0
    recency_consistency: float = 0.0
    noise_penalty_adjusted: float = 1.0
    VTM: float = 0.0


# ---------------------------------------------------------------------------
# Trend reasoning
# ---------------------------------------------------------------------------
class TrendReason(BaseModel):
    ai_trend_reason: str = ""
    key_trend_signals: list[str] = Field(default_factory=list)
    evidence_summary: str = ""
    source_summary: str = ""
    catalyst_summary: str = ""
    counter_signals: list[str] = Field(default_factory=list)
    confidence_level: str = "low"    # low | moderate | high
    recommended_next_validation: str = ""


# ---------------------------------------------------------------------------
# LLM structured extraction schema (kept small / robust)
# ---------------------------------------------------------------------------
class LLMProduct(BaseModel):
    product_name: str
    vertical: str = ""
    category: str = ""
    brand: str = ""
    evidence_snippet: str = ""
    scarcity_language: str = ""
    release_date: str = ""
    confidence: float = 0.5


class LLMProductList(BaseModel):
    products: list[LLMProduct] = Field(default_factory=list)
