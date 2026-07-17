"""News Intelligence Agent.

Scans external news pages for events that may drive collectible demand, extracts
entities, and maps them to candidate products. Deterministic by default with an
optional LLM assist for mapping.
"""
from __future__ import annotations

import re
from typing import Optional

from ..config import Config
from ..llm.client import LLMClient
from ..llm.prompts import NEWS_MAPPING_SYSTEM, NEWS_MAPPING_USER
from ..logging_config import get_logger
from ..models import FetchedPage, NewsSignal
from ..taxonomy import detect_event_type, detect_signals, map_vertical, snippet_around
from ..util import registered_domain, utc_iso

log = get_logger("agent.news")

# proper-noun-ish entity grabs (players, characters, teams, franchises)
_PROPER = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")

_MAPPING_RULES = [
    # (trigger words, product template)
    (["rookie", "debut", "drafted"], "{entity} rookie cards"),
    (["mvp", "award", "wins", "champion", "record"], "{entity} cards / autographs / memorabilia"),
    (["retire", "retirement", "final game"], "{entity} memorabilia and autographs"),
    (["trailer", "movie", "premiere", "film"], "{entity} comics / figures / cards"),
    (["sells out", "sold out", "restock"], "{entity} sealed products"),
    (["auction", "sold for", "record price"], "{entity} related collectibles"),
    (["anniversary"], "{entity} anniversary editions"),
    (["discontinued", "discontinuation"], "{entity} secondary-market pieces"),
]


class NewsIntelligenceAgent:
    def __init__(self, config: Config, llm: Optional[LLMClient] = None):
        self.config = config
        self.verticals = config.verticals
        self.llm = llm

    def analyze(self, page: FetchedPage, source_credibility: float = 0.5) -> Optional[NewsSignal]:
        if page.status != "ok" or not page.text:
            return None
        text = (page.title + ". " + page.text[:1500])
        low = text.lower()
        signals = detect_signals(text)
        event_type, ev_terms = detect_event_type(text)
        vertical, vscore, _ = map_vertical(text, self.verticals)

        # need at least a news catalyst OR a collectible signal
        if signals["news_catalyst_score"] == 0 and vscore == 0:
            return None

        # entity detection
        entities = _PROPER.findall(page.title) or _PROPER.findall(page.text[:600])
        entity = entities[0] if entities else (vertical or "collectibles")

        # deterministic mapping
        mapped: list[str] = []
        reason_bits: list[str] = []
        for triggers, template in _MAPPING_RULES:
            if any(t in low for t in triggers):
                mapped.append(template.format(entity=entity))
                reason_bits.append(f"'{[t for t in triggers if t in low][0]}' → {template.format(entity=entity)}")
        if not mapped and vertical:
            mapped.append(f"{entity} {vertical.lower()}")

        news_score = min(1.0, 0.5 * signals["news_catalyst_score"] + 0.3 * vscore
                         + 0.2 * (1 if event_type else 0))

        ns = NewsSignal(
            news_url=page.url,
            news_title=page.title,
            source_domain=page.domain or registered_domain(page.url),
            entity_detected=entity,
            mapped_product_candidates=mapped[:5],
            news_signal_score=round(news_score, 3),
            news_to_product_reason="; ".join(reason_bits) or f"Collectible relevance for {entity}.",
            source_credibility_score=round(source_credibility, 3),
            event_type=event_type,
            observed_at=utc_iso(),
        )

        # optional LLM refinement
        if self.llm and self.llm.available and news_score < 0.4:
            self._llm_refine(ns, page)
        return ns

    def _llm_refine(self, ns: NewsSignal, page: FetchedPage) -> None:
        try:
            user = NEWS_MAPPING_USER.format(title=page.title, snippet=page.text[:600])
            data = self.llm.complete_json(NEWS_MAPPING_SYSTEM, user)
        except Exception:
            return
        if not data:
            return
        if data.get("entity_detected"):
            ns.entity_detected = data["entity_detected"]
        if data.get("event_type"):
            ns.event_type = data["event_type"]
        cands = data.get("mapped_product_candidates") or []
        if cands:
            ns.mapped_product_candidates = list(dict.fromkeys(ns.mapped_product_candidates + cands))[:6]
        if data.get("news_to_product_reason"):
            ns.news_to_product_reason = data["news_to_product_reason"]
