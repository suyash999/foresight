"""Product Extraction Agent.

Extracts structured collectible product candidates from a fetched page using a
high-recall ladder:
  1. JSON-LD / schema.org Product & Article entities
  2. Page title + headings (if collectible signal present)
  3. Sentence/line scan for product-like mentions (year + brand/franchise)
  4. Optional LLM assist for messy pages

Every candidate is enriched with vertical/category, signal language, geography,
and vertical-specific fields (card / coin / comic / toy / watch / vinyl).
"""
from __future__ import annotations

import re
from typing import Optional

from ..config import Config
from ..llm.client import LLMClient
from ..llm.prompts import (PRODUCT_EXTRACTION_SYSTEM, PRODUCT_EXTRACTION_USER)
from ..logging_config import get_logger
from ..models import FetchedPage, LLMProductList, ProductCandidate
from ..taxonomy import (CATEGORY_HINTS, detect_signals, map_category, map_vertical,
                        snippet_around)
from ..util import (canonicalize_name, canonical_key, clean_text, now_utc,
                    to_ist_iso, utc_iso)

log = get_logger("agent.extract")

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_PRICE = re.compile(r"(?:US)?\$\s?([\d,]+(?:\.\d{2})?)|£\s?([\d,]+)|€\s?([\d,]+)")
_SERIAL = re.compile(r"\b\d{1,4}\s?/\s?\d{1,4}\b")
_GRADE = re.compile(r"\b(PSA|BGS|SGC|CGC)\s?(\d{1,2}(?:\.\d)?)\b", re.IGNORECASE)
_REF = re.compile(r"\bref(?:erence)?\.?\s?([A-Z0-9\-\.]{3,})\b", re.IGNORECASE)

_COUNTRY_HINTS = {
    "United States": ["usa", "u.s.", "american", "united states", "mlb", "nba", "nfl"],
    "United Kingdom": ["uk", "british", "england", "premier league"],
    "Japan": ["japan", "japanese", "tokyo", "pokemon company"],
    "Germany": ["germany", "german", "bundesliga"],
    "France": ["france", "french", "ligue 1"],
    "Canada": ["canada", "canadian", "nhl"],
    "Italy": ["italy", "italian", "serie a"],
    "Switzerland": ["swiss", "switzerland", "geneva"],
}

_BRANDS = ["topps", "panini", "bowman", "upper deck", "pokemon", "wizards of the coast",
           "bandai", "lego", "funko", "hasbro", "mattel", "rolex", "omega", "patek philippe",
           "audemars piguet", "marvel", "dc", "fanatics", "leaf", "sage", "prizm", "select",
           "mosaic", "optic", "chrome"]

# Product-noun anchors and words that signal the *end* of a product phrase.
_LEADING_STOP = {"the", "a", "an", "these", "this", "that", "other", "for", "with",
                 "in", "on", "of", "and", "while", "meaning", "context", "each",
                 "both", "new", "some", "all"}
_TRAILING_BREAK = {"while", "which", "that", "meaning", "means", "because", "with",
                   "include", "including", "combine", "combines", "average", "averaged",
                   "and", "arrive", "arrives", "featuring", "since", "after", "before",
                   "when", "where", "per", "are", "is", "was", "were", "have", "has"}


def derive_product_name(line: str, brand: str = "") -> str:
    """Turn a scanned sentence into a concise product-like name (keeps recall,
    cuts the sentence noise). Prefers a phrase anchored on a year or brand."""
    words = re.sub(r"[|•]", " ", line).split()
    # strip leading filler words
    while words and words[0].lower().strip(",.:;-") in _LEADING_STOP:
        words = words[1:]
    if not words:
        return clean_text(line, 80)
    # anchor on the first year token — but only if real content follows it
    # (a trailing "…debut in 2026" should NOT collapse to just "2026").
    start = 0
    for i, w in enumerate(words):
        if _YEAR.fullmatch(w.strip(",.:;")) and (len(words) - i) >= 4:
            start = i
            break
    phrase = words[start:start + 9]
    # cut at the first trailing connector after a few tokens
    out = []
    for i, w in enumerate(phrase):
        if i >= 3 and w.lower().strip(",.:;") in _TRAILING_BREAK:
            break
        out.append(w)
    name = " ".join(out).strip(" -–—:,.")
    if len(name) < 4:  # fall back to a trimmed slice of the line
        name = clean_text(line, 80)
    return clean_text(name, 90)


class ProductExtractionAgent:
    def __init__(self, config: Config, llm: Optional[LLMClient] = None):
        self.config = config
        self.verticals = config.verticals
        self.llm = llm

    # -- helpers ------------------------------------------------------------
    def _detect_country(self, text: str) -> tuple[str, float]:
        t = text.lower()
        for country, hints in _COUNTRY_HINTS.items():
            if any(h in t for h in hints):
                return country, 0.6
        return "", 0.3

    def _detect_brand(self, text: str) -> str:
        t = text.lower()
        for b in _BRANDS:
            if b in t:
                return b.title()
        return ""

    def _fill_card_fields(self, pc: ProductCandidate, text: str) -> None:
        t = text.lower()
        pc.rookie_card_flag = "rookie" in t or " rc " in f" {t} "
        pc.autograph_flag = "autograph" in t or " auto " in f" {t} "
        pc.memorabilia_flag = "memorabilia" in t or "patch" in t or "relic" in t
        pc.serial_numbered_flag = bool(_SERIAL.search(text)) or "numbered" in t
        m = _SERIAL.search(text)
        if m:
            pc.print_run = m.group(0).replace(" ", "")
        g = _GRADE.search(text)
        if g:
            pc.grading_company = g.group(1).upper()
            pc.grade = g.group(2)
        for box, flag in [("hobby box", "hobby_box_flag"), ("blaster", "blaster_box_flag"),
                          ("booster box", "booster_box_flag"), ("case", "case_flag")]:
            if box in t:
                setattr(pc, flag, True)
        if any(x in t for x in ["sealed", "booster box", "hobby box", "blaster"]):
            pc.sealed_product_type = "sealed box"
        if "short print" in t or " sp " in f" {t} ":
            pc.parallel_variant = pc.parallel_variant or "short print"
        for par in ["prizm", "chrome", "refractor", "parallel", "holo", "rainbow"]:
            if par in t:
                pc.parallel_variant = pc.parallel_variant or par
        y = _YEAR.search(text)
        if y:
            pc.release_year = y.group(0)

    def _fill_other_fields(self, pc: ProductCandidate, text: str) -> None:
        t = text.lower()
        if pc.vertical == "Coins":
            y = _YEAR.search(text)
            if y:
                pc.coin_year = y.group(0)
            for mint in ["philadelphia", "denver", "san francisco", "royal mint", "us mint"]:
                if mint in t:
                    pc.coin_mint = mint.title()
        if pc.vertical == "Comic Books and Memorabilia":
            m = re.search(r"#(\d{1,4})", text)
            if m:
                pc.comic_issue_number = m.group(1)
            for pub in ["marvel", "dc", "image", "dark horse", "idw"]:
                if pub in t:
                    pc.comic_publisher = pub.title()
        if pc.vertical == "Watches":
            r = _REF.search(text)
            if r:
                pc.watch_reference_number = r.group(1)
            pc.watch_model = self._detect_brand(text)
        if pc.vertical == "Vinyl Records":
            pc.limited_edition_flag = "limited" in t or "record store day" in t
        if "limited" in t or "exclusive" in t:
            pc.limited_edition_flag = True

    def _build_candidate(self, name: str, context: str, page: FetchedPage,
                         method: str, confidence: float) -> Optional[ProductCandidate]:
        name = clean_text(name, 200)
        if not name or len(name) < 4:
            return None
        combo = f"{name} {context}"
        vertical, vscore, _ = map_vertical(combo, self.verticals)
        if not vertical:
            # high recall: still accept if page has any collectible signal
            vertical, vscore, _ = map_vertical(page.title + " " + page.text[:500], self.verticals)
        signals = detect_signals(combo)
        pc = ProductCandidate(
            product_name=name,
            canonical_product_name=canonicalize_name(name),
            vertical=vertical,
            category=map_category(combo, vertical),
            brand=self._detect_brand(combo),
            source_url=page.url,
            source_domain=page.domain,
            extraction_method=method,
            extraction_confidence=round(confidence, 3),
            evidence_snippet=clean_text(context, 220),
            scarcity_language=", ".join(signals["scarcity_hits"][:4]),
            demand_language=", ".join(signals["demand_hits"][:4]),
            auction_or_sales_signal=", ".join(signals["marketplace_hits"][:4]),
            news_signal=", ".join(signals["news_hits"][:4]),
            observed_at_utc=utc_iso(),
            observed_at_ist=to_ist_iso(tz_name=self.config.timezone),
            limited_edition_flag="limited" in combo.lower(),
        )
        # geography
        country, geo_conf = self._detect_country(combo)
        pc.detected_country = country
        pc.source_country = country
        pc.geo_confidence = geo_conf
        scope = self.config.get("geography.default_market_scope", "global")
        pc.market_scope = scope
        pc.global_signal_flag = scope == "global" or not country
        pc.local_signal_flag = bool(country) and scope != "global"
        # price
        pm = _PRICE.search(combo)
        if pm:
            val = next((g for g in pm.groups() if g), None)
            if val:
                try:
                    pc.price = float(val.replace(",", ""))
                    pc.currency = "USD" if "$" in pm.group(0) else ("GBP" if "£" in pm.group(0) else "EUR")
                except Exception:
                    pass
        # release date
        y = _YEAR.search(combo)
        if y:
            pc.release_year = y.group(0)
        if signals["release_hits"]:
            pc.release_date = pc.release_date or ""
        # vertical-specific enrichment
        if vertical == "Trading Cards":
            self._fill_card_fields(pc, combo)
        self._fill_other_fields(pc, combo)
        return pc

    # -- main ---------------------------------------------------------------
    def extract(self, page: FetchedPage) -> list[ProductCandidate]:
        if page.status != "ok" or not page.text:
            return []
        candidates: list[ProductCandidate] = []
        seen: set[str] = set()

        def add(pc: Optional[ProductCandidate]):
            if pc is None:
                return
            key = canonical_key(pc.product_name)
            if key and key not in seen:
                seen.add(key)
                candidates.append(pc)

        # 1. JSON-LD products / articles
        for entity in page.jsonld:
            etype = entity.get("@type", "")
            etypes = etype if isinstance(etype, list) else [etype]
            name = entity.get("name") or entity.get("headline") or ""
            if not name:
                continue
            if any(t in ("Product", "IndividualProduct", "Article", "NewsArticle",
                         "CreativeWork") for t in etypes):
                desc = entity.get("description", "")
                add(self._build_candidate(name, f"{name} {desc}", page, "jsonld", 0.75))

        # 2. Title
        if page.title:
            add(self._build_candidate(page.title, page.title + " " + page.text[:400],
                                      page, page.extraction_method or "title", 0.55))

        # 3. Line scan (year + brand/franchise or strong signal)
        lines = re.split(r"[\n\.•|]", page.text)
        for line in lines:
            line = line.strip()
            if len(line) < 12 or len(line) > 220:
                continue
            low = line.lower()
            has_year = bool(_YEAR.search(line))
            has_brand = any(b in low for b in _BRANDS)
            v, vscore, _ = map_vertical(line, self.verticals)
            if v and (has_year or has_brand or vscore >= 0.5):
                # concise product name, full line kept as evidence context
                name = derive_product_name(line, self._detect_brand(line))
                add(self._build_candidate(name, line, page, "text-scan", 0.4 + 0.1 * vscore))
            if len(candidates) >= 60:  # per-page cap (still high recall)
                break

        # 4. Optional LLM assist for messy / low-yield pages
        if self.llm and self.llm.available and len(candidates) < 3 and len(page.text) > 400:
            add_from = self._llm_extract(page)
            for pc in add_from:
                add(pc)

        return candidates

    def _llm_extract(self, page: FetchedPage) -> list[ProductCandidate]:
        try:
            user = PRODUCT_EXTRACTION_USER.format(
                verticals=", ".join(self.verticals), title=page.title,
                url=page.url, text=page.text[:4000])
            result = self.llm.complete_model(PRODUCT_EXTRACTION_SYSTEM, user, LLMProductList)
        except Exception as exc:
            log.debug("LLM extract failed: %s", exc)
            return []
        if not result:
            return []
        out = []
        for p in result.products:
            pc = self._build_candidate(p.product_name, p.evidence_snippet or p.product_name,
                                       page, "llm", max(0.5, p.confidence))
            if pc:
                if p.vertical:
                    pc.vertical = p.vertical
                if p.brand:
                    pc.brand = p.brand
                out.append(pc)
        return out
