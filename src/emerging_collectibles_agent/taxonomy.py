"""Deterministic collectibles intelligence: keyword banks, vertical mapping,
signal detection, and event-type detection.

This is the non-LLM brain shared by discovery, page scoring, extraction, news
mapping, and event detection. It is intentionally broad (high recall) — the
dashboard filters do the narrowing, not this layer.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Vertical keyword banks (broad, high recall)
# ---------------------------------------------------------------------------
VERTICAL_KEYWORDS: dict[str, list[str]] = {
    "Trading Cards": [
        "trading card", "tcg", "rookie card", "topps", "panini", "pokemon card",
        "pokémon", "magic: the gathering", "mtg", "yu-gi-oh", "yugioh", "one piece card",
        "lorcana", "booster box", "hobby box", "blaster", "graded card", "psa 10",
        "bgs", "sgc", "autograph card", "parallel", "short print", "serial numbered",
        "prizm", "chrome", "bowman", "checklist", "sealed box", "case break", "pack",
    ],
    "Coins": [
        "coin", "numismatic", "mint", "bullion", "commemorative coin", "proof coin",
        "silver dollar", "gold coin", "ngc", "pcgs", "error coin", "krugerrand",
        "sovereign", "penny", "nickel", "double eagle",
    ],
    "Collectibles": [
        "collectible", "limited edition", "collector", "exclusive drop", "memorabilia",
        "rare item", "collectible market",
    ],
    "Comic Books and Memorabilia": [
        "comic", "cgc", "key issue", "first appearance", "marvel comic", "dc comic",
        "graphic novel", "variant cover", "golden age", "silver age", "omnibus",
        "comic book", "first print",
    ],
    "Toys and Hobbies": [
        "action figure", "lego", "funko", "funko pop", "toy", "model kit", "die-cast",
        "hot wheels", "hasbro", "mattel", "plush", "collectible toy", "figure",
    ],
    "Vinyl Records": [
        "vinyl", "lp", "record store day", "reissue", "pressing", "colored vinyl",
        "album", "gatefold", "180g", "repress", "vinyl record",
    ],
    "Watches": [
        "watch", "rolex", "omega", "patek", "audemars", "reference number", "chronograph",
        "dial", "bezel", "wristwatch", "horology", "tudor", "seiko", "cartier",
    ],
    "Sports Memorabilia": [
        "jersey", "game-used", "game worn", "signed ball", "championship ring",
        "sports memorabilia", "match worn", "helmet", "cleats", "pennant",
    ],
    "Entertainment Memorabilia": [
        "movie prop", "screen used", "poster", "autographed photo", "film memorabilia",
        "prop auction", "costume", "script", "convention exclusive",
    ],
    "Autographs": [
        "autograph", "signed", "signature", "hand-signed", "authenticated signature",
        "jsa", "psa/dna", "beckett authentication",
    ],
}

CATEGORY_HINTS: dict[str, list[str]] = {
    "Baseball cards": ["baseball card", "mlb card", "bowman", "topps chrome baseball"],
    "Basketball cards": ["basketball card", "nba card", "prizm basketball", "wembanyama"],
    "Football cards": ["football card", "nfl card", "panini prizm football"],
    "Soccer cards": ["soccer card", "football card panini", "topps match attax", "messi card"],
    "Hockey cards": ["hockey card", "nhl card", "upper deck hockey"],
    "Formula 1 cards": ["f1 card", "formula 1 card", "topps f1"],
    "UFC cards": ["ufc card", "panini ufc"],
    "WWE cards": ["wwe card", "panini wwe", "wrestling card"],
    "Pokemon cards": ["pokemon card", "pokémon", "charizard", "pokemon tcg", "151 set"],
    "Magic: The Gathering": ["magic the gathering", "mtg", "commander deck"],
    "Yu-Gi-Oh!": ["yu-gi-oh", "yugioh"],
    "One Piece TCG": ["one piece card", "one piece tcg", "op card game"],
    "Lorcana": ["lorcana", "disney lorcana"],
    "Graded cards": ["psa 10", "graded card", "bgs", "cgc card", "sgc"],
    "Sealed boxes": ["sealed box", "booster box", "hobby box", "blaster box", "case"],
    "Rare coins": ["rare coin", "key date coin", "error coin"],
    "Luxury watches": ["rolex", "patek", "audemars piguet", "omega", "luxury watch"],
    "Key issues": ["key issue", "first appearance", "comic key"],
    "Funko Pop": ["funko pop", "funko exclusive"],
    "LEGO": ["lego set", "lego release"],
    "Record Store Day": ["record store day", "rsd exclusive"],
}

# ---------------------------------------------------------------------------
# Signal language banks
# ---------------------------------------------------------------------------
SCARCITY_PATTERNS = [
    "sold out", "limited edition", "low print run", "numbered", "short print",
    "discontinued", "allocated", "exclusive", "hard to find", "out of stock",
    "limited run", "one of one", "1/1", "rare", "scarce", "print run of",
]
DEMAND_PATTERNS = [
    "in demand", "hot", "trending", "skyrocket", "surge", "spike", "flying off",
    "high demand", "must have", "chase card", "grail", "waitlist", "backorder",
]
MARKETPLACE_PATTERNS = [
    "auction", "sold for", "hammer price", "record price", "sales report", "ranking",
    "top seller", "preorder", "pre-order", "price guide", "market value", "resale",
    "secondary market", "realized price", "final bid",
]
RELEASE_PATTERNS = [
    "release date", "releases on", "coming soon", "launch", "debut", "drops on",
    "available now", "new set", "upcoming", "pre-order", "release calendar",
    "hits shelves", "street date", "out now",
]
NEWS_CATALYST_PATTERNS = [
    "record", "milestone", "mvp", "championship", "wins", "trailer", "announced",
    "anniversary", "retirement", "debut", "injury", "comeback", "hall of fame",
    "auction record", "collaboration", "passed away", "memorial", "sells out",
    "world cup", "super bowl", "finals", "olympics", "wrestlemania",
]

# ---------------------------------------------------------------------------
# Event type detection
# ---------------------------------------------------------------------------
EVENT_TYPE_PATTERNS: dict[str, list[str]] = {
    "product release": ["release date", "new set", "launches", "hits shelves", "street date"],
    "preorder launch": ["pre-order", "preorder", "now available to order"],
    "official announcement": ["announced", "unveiled", "reveals", "official announcement"],
    "sports milestone": ["milestone", "record", "breaks record", "career high"],
    "championship": ["championship", "finals", "title", "champions"],
    "tournament": ["tournament", "playoffs", "world cup", "grand slam"],
    "award": ["mvp", "award", "wins award", "hall of fame", "rookie of the year"],
    "player debut": ["debut", "makes debut", "first game"],
    "player retirement": ["retire", "retirement", "final game"],
    "record-breaking performance": ["record-breaking", "sets record", "breaks record"],
    "movie release": ["movie release", "premieres", "in theaters", "hits theaters"],
    "trailer release": ["trailer", "teaser trailer", "first look"],
    "TV show release": ["series premiere", "new season", "streaming release"],
    "comic release": ["comic release", "new issue", "#1 comic"],
    "anniversary": ["anniversary", "th anniversary", "years since"],
    "celebrity news": ["passed away", "memorial", "celebrity", "death"],
    "collaboration": ["collaboration", "collab", "x ", "partnership", "teams up"],
    "limited drop": ["limited drop", "exclusive drop", "limited edition drop"],
    "auction result": ["auction", "sold for", "hammer price", "record price", "realized"],
    "sold-out event": ["sold out", "out of stock", "allocation"],
    "price spike": ["price spike", "surge", "skyrocket", "prices jump"],
    "manufacturer discontinuation": ["discontinued", "discontinuation", "no longer produced"],
    "grading/population report event": ["population report", "pop report", "graded population"],
    "convention/event appearance": ["comic-con", "toy fair", "convention", "expo", "fair"],
    "cultural trend": ["viral", "trend", "cultural moment", "goes viral"],
}

_ALL_VERTICAL_TERMS = {v: [t.lower() for t in terms] for v, terms in VERTICAL_KEYWORDS.items()}


def _count_hits(text: str, patterns: list[str]) -> tuple[int, list[str]]:
    hits = []
    for p in patterns:
        if p in text:
            hits.append(p)
    return len(hits), hits


def map_vertical(text: str, allowed_verticals: list[str] | None = None) -> tuple[str, float, dict]:
    """Return (best_vertical, score 0..1, per-vertical hit counts)."""
    t = (text or "").lower()
    scores: dict[str, int] = {}
    for vertical, terms in _ALL_VERTICAL_TERMS.items():
        if allowed_verticals and vertical not in allowed_verticals:
            continue
        n, _ = _count_hits(t, terms)
        if n:
            scores[vertical] = n
    if not scores:
        return "", 0.0, {}
    best = max(scores, key=scores.get)
    # normalise: 1 hit -> 0.4, 2 -> 0.6, 3+ -> up to 1.0
    n = scores[best]
    score = min(1.0, 0.3 + 0.2 * n)
    return best, score, scores


def map_category(text: str, vertical: str = "") -> str:
    t = (text or "").lower()
    best = ""
    best_hits = 0
    for cat, hints in CATEGORY_HINTS.items():
        n, _ = _count_hits(t, [h.lower() for h in hints])
        if n > best_hits:
            best_hits = n
            best = cat
    return best


def detect_signals(text: str) -> dict[str, Any]:
    """Detect scarcity/demand/marketplace/release/news signals with snippets."""
    t = (text or "").lower()
    sc_n, sc = _count_hits(t, SCARCITY_PATTERNS)
    dm_n, dm = _count_hits(t, DEMAND_PATTERNS)
    mk_n, mk = _count_hits(t, MARKETPLACE_PATTERNS)
    rl_n, rl = _count_hits(t, RELEASE_PATTERNS)
    nw_n, nw = _count_hits(t, NEWS_CATALYST_PATTERNS)
    return {
        "scarcity_hits": sc, "scarcity_score": min(1.0, 0.3 * sc_n),
        "demand_hits": dm, "demand_score": min(1.0, 0.3 * dm_n),
        "marketplace_hits": mk, "marketplace_score": min(1.0, 0.25 * mk_n),
        "release_hits": rl, "release_score": min(1.0, 0.3 * rl_n),
        "news_hits": nw, "news_catalyst_score": min(1.0, 0.25 * nw_n),
    }


def detect_event_type(text: str) -> tuple[str, list[str]]:
    """Return (best_event_type, matched terms) or ('', [])."""
    t = (text or "").lower()
    best_type = ""
    best_hits: list[str] = []
    best_n = 0
    for etype, patterns in EVENT_TYPE_PATTERNS.items():
        n, hits = _count_hits(t, patterns)
        if n > best_n:
            best_n = n
            best_type = etype
            best_hits = hits
    return best_type, best_hits


def snippet_around(text: str, terms: list[str], width: int = 160) -> str:
    if not text or not terms:
        return ""
    low = text.lower()
    for term in terms:
        idx = low.find(term)
        if idx >= 0:
            start = max(0, idx - width // 2)
            end = min(len(text), idx + len(term) + width // 2)
            frag = text[start:end].strip()
            return re.sub(r"\s+", " ", frag)
    return ""


def any_collectible_signal(text: str) -> bool:
    v, score, _ = map_vertical(text)
    return score > 0
