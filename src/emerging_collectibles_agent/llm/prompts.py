"""Versioned prompt templates for optional LLM tasks.

Prompt versions are tracked in `prompt_performance` so the self-learning layer
can compare versions over time.
"""
from __future__ import annotations

EXTRACTION_PROMPT_VERSION = "v1"
REASONING_PROMPT_VERSION = "v1"
NEWS_MAPPING_PROMPT_VERSION = "v1"

PRODUCT_EXTRACTION_SYSTEM = (
    "You are a precise collectibles product-extraction engine. Extract concrete "
    "collectible PRODUCTS (not generic topics) mentioned in the page text. "
    "Return ONLY valid JSON matching the schema. Do not invent products that are "
    "not supported by the text."
)

PRODUCT_EXTRACTION_USER = """Extract collectible products from the following page.

Verticals in scope: {verticals}

Return JSON of the form:
{{"products": [
  {{"product_name": "...", "vertical": "...", "category": "...", "brand": "...",
    "evidence_snippet": "<= 200 chars quote from text", "scarcity_language": "...",
    "release_date": "...", "confidence": 0.0}}
]}}

Only include products actually supported by the text. Empty list is valid.

TITLE: {title}
URL: {url}
TEXT:
{text}
"""

TREND_REASONING_SYSTEM = (
    "You are a collectibles market analyst. Explain, using ONLY the provided "
    "evidence, why a product may be emerging. Never fabricate sources or events. "
    "Return ONLY valid JSON."
)

TREND_REASONING_USER = """Given the evidence, write an evidence-backed reason.

Product: {product_name}
EPS: {eps}  VTM: {vtm}
Source count: {source_count}  Source types: {source_types}
Catalyst / event: {catalyst}
Scarcity signals: {scarcity}
Evidence snippets: {snippets}

Return JSON:
{{"ai_trend_reason": "...", "key_trend_signals": ["..."],
  "evidence_summary": "...", "catalyst_summary": "...",
  "counter_signals": ["..."], "confidence_level": "low|moderate|high",
  "recommended_next_validation": "..."}}
"""

NEWS_MAPPING_SYSTEM = (
    "You map news events to potentially trending collectible products. Only map "
    "when the text plausibly supports it. Return ONLY valid JSON."
)

NEWS_MAPPING_USER = """Map this news item to collectible product candidates.

Headline: {title}
Snippet: {snippet}

Return JSON:
{{"entity_detected": "...", "event_type": "...",
  "mapped_product_candidates": ["..."], "news_to_product_reason": "..."}}
"""
