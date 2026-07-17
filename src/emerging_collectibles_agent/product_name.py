"""Product-name detection & cleaning.

The high-recall line/title scan can surface prose, news headlines, and spec-sheet
fragments. This module gates them: only strings that look like a real collectible
PRODUCT (brand / product line / parallel / set + supporting signal) pass, and it
cleans article-y suffixes ("… Full Review & Checklist", "… - ESPN").

Deterministic and fast; used before any candidate becomes a product.
"""
from __future__ import annotations

import re

# ---- brand / product-line / parallel vocab (positive signals) --------------
_BRAND_TOKENS = [
    "panini", "topps", "bowman", "upper deck", "fleer", "donruss", "leaf", "sage",
    "prizm", "optic", "mosaic", "select", "obsidian", "chronicles", "contenders",
    "immaculate", "national treasures", "flawless", "spectra", "phoenix", "absolute",
    "certified", "prestige", "score", "chrome", "heritage", "stadium club", "gallery",
    "allen & ginter", "gypsy queen", "finest", "sapphire", "pokemon", "pokémon",
    "magic the gathering", "yu-gi-oh", "yugioh", "one piece", "lorcana", "funko",
    "lego", "hot wheels", "hasbro", "mattel", "rolex", "omega", "patek", "seiko",
    "marvel", "dc comics", "star wars", "pwcc", "goldin",
    # coins
    "morgan", "peace dollar", "silver eagle", "gold eagle", "double eagle",
    "buffalo nickel", "lincoln cent", "krugerrand", "sovereign", "proof set",
    "commemorative", "us mint", "royal mint",
    # vinyl / music
    "vinyl", "reissue", "repress", "record store day", "colored vinyl", "180g",
    # comics
    "cgc", "cbcs", "omnibus", "variant cover", "first appearance",
    # watches (extra)
    "tudor", "cartier", "audemars", "grand seiko", "speedmaster", "submariner",
]
_PRODUCT_TYPE = [
    "hobby box", "blaster box", "booster box", "retail box", "mega box", "hanger box",
    "hobby boxes", "booster pack", "box set", "sealed box", "hobby case", "case",
    "tin", "bundle", "starter deck", "elite trainer box", "collection box",
]
_PARALLEL_INSERT = [
    "prizm", "refractor", "parallel", "parallels", "insert", "rookie", "auto",
    "autograph", "numbered", "short print", "ssp", "1-of-1", "1/1", "cracked ice",
    "pandora", "hyper", "mojo", "disco", "wave", "color blast", "downtown",
    "kaboom", "optichrome", "holo", "reverse holo", "alternate art", "secret rare",
    "serial", "die-cut", "relic", "patch",
]

_BRAND_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _BRAND_TOKENS) + r")\b", re.IGNORECASE)
_TYPE_RE = re.compile("|".join(re.escape(t) for t in _PRODUCT_TYPE), re.IGNORECASE)
_PARALLEL_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _PARALLEL_INSERT) + r")\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}(?:-\d{2})?\b")

# ---- negative signals (prose / news / spec-sheet) --------------------------
_NEWS_SUFFIX_RE = re.compile(r"\s[-–—]\s(ESPN|CBS(?:\s*Sports)?|Fox\s*Sports|Sky\s*Sports|"
                             r"BBC|Reuters|AP|Yahoo|Bleacher\s*Report|The\s*Athletic)\s*$",
                             re.IGNORECASE)
# sentence verbs / news phrasing → this is a sentence, not a name
_PROSE_RE = re.compile(
    r"\b(calls?\s+out|call(s|ed)?|beat(s|en)?|awarded|sells?\s+for|sold\s+for|rank(ing|s|ed)?|"
    r"narrate|powers?|crossed|deliver(s|ed)?|guarantee(s|d)?|emphasi[sz]e(s|d)?|suits?|"
    r"combine(s|d)?|offer(s|ed)?|feature(s|d)?|continue(s|d)?|hold(s|ing)?|held|pokes?|"
    r"match(es|ed)?|win(s|ning)?|induct(ed|s)?|retire(s|d|ment)?|announce(s|d)?\s+that|"
    r"helping|returns?(\s+several)?|receives?|carry|carries|excel|makes?\s+them|"
    r"mark(s|ed)?|follow(s|ed)?|confirm(ed|s)?|preview(s|ed)?|mov(e|es|ed|ing)|"
    r"will\s+be|would|includes?|contains?|comes?|brings?|gives?|shows?|step\s+up|"
    r"suit(s|ed)?|to\s+narrate|to\s+be\s+awarded|driven\s+by)\b",
    re.IGNORECASE)
# prose markers / section headers / editorial phrasing
_MARKER_RE = re.compile(
    r"(notes?:|overview|worth\s+noting|distribution|value\s*&\s*collectibility|"
    r"check\s+official|pack\s+odds|deeper\s+look|year-on-year|subscription|"
    r"exclusive\)|\(exclusive|comprised\s+of|it\s+(suits|emphasi|continues)|"
    r"we\s+have|i['’]ve\s+been|so\s+if\s+you|these\s+product|typically\s+retail|"
    r"key\s+insert\s+sets|additional\s+possibilities|visually\s+pleasing|"
    r"host\s+nation|full\s+player|player-by-player|first\s+\d+\s+guests|"
    r"quarter\s+and\s+streaming|template:|gallery:|\btbr\b|during\s+(his|her)|"
    r"first\s+year|first\s+appearance\s+in|meaningful\s+step|post-checklist|"
    r"illustrated\s+portraits|refractive\s+chrome|chromium\s+template|"
    r"familiar\s+chromium|standard\s+chromium|decision\s+to|entry-level|"
    r"case\s+hits|stock\s+shines|signature\s+chromium|often\s+/\d)",
    re.IGNORECASE)
# pure quantity / spec-sheet lines
_QUANTITY_RE = re.compile(
    r"(\d+\s*cards?\s*(per|total|/)|\bcards?/pack\b|\bpacks?/box\b|\d+\s*cards?/pack|"
    r"\d+\s*packs?/box|\d+\s*cards?\s*per\s*pack|^\d+\s*cards?\b|~?\d+\s*base\b)",
    re.IGNORECASE)

# leading tokens that indicate a fragment / section header (when no brand present)
_LEADING_STOP = {"the", "a", "an", "these", "this", "it", "we", "from", "comprised",
                 "worth", "additional", "standard", "every", "teams", "host", "numbering",
                 "inserts", "distribution", "value", "exact", "true", "beautiful",
                 "retail", "features", "format", "classic", "fan-favorite", "design",
                 "explosive", "anime", "multicolor", "worth", "one", "both", "sets",
                 "visually", "key", "card", "cards", "12", "144", "first", "full",
                 "parallels", "numbered", "hobby"}

# article-y suffixes to strip from otherwise-good names
_SUFFIX_STRIP = re.compile(
    r"\s*(?:[-–—]\s*)?(full\s+review\s*&?\s*(?:amp;)?\s*checklist|set\s+review\s+and\s+checklist|"
    r"box\s+set\s+review\s+and\s+checklist|review\s+and\s+checklist|set\s+review|"
    r"\breview\b|\bchecklist\b|full\s+review|\(exclusive\)|free|factory\s+sealed.*$)\s*$",
    re.IGNORECASE)
_EMOJI_RE = re.compile("[\U0001F1E6-\U0001FAFF☀-➿]")


def clean_product_name(name: str) -> str:
    if not name:
        return ""
    n = _EMOJI_RE.sub("", name).strip()
    n = re.sub(r"&#0?38;|&amp;", "&", n)
    # strip trailing article phrases (repeat a couple times for stacked suffixes)
    for _ in range(3):
        new = _SUFFIX_STRIP.sub("", n).strip(" -–—:,")
        if new == n:
            break
        n = new
    return re.sub(r"\s+", " ", n).strip(" -–—:,")


def product_name_quality(name: str) -> tuple[float, str]:
    """Return (score 0..1, reason). >= threshold ⇒ looks like a real product."""
    if not name:
        return 0.0, "empty"
    n = clean_product_name(name)
    if not n:
        return 0.0, "empty-after-clean"
    low = n.lower()
    tokens = n.split()

    # hard rejects
    if _NEWS_SUFFIX_RE.search(name):
        return 0.0, "news-source-suffix"
    if _QUANTITY_RE.search(low):
        return 0.0, "quantity/spec-line"
    if _MARKER_RE.search(low):
        return 0.0, "prose-marker"
    if _PROSE_RE.search(low):
        return 0.0, "sentence-verb"
    if ";" in n:
        return 0.0, "semicolon-prose"
    if len(tokens) < 2 or len(tokens) > 10:
        return 0.0, "too-short-or-long"
    if not re.search(r"[A-Za-z]", n):
        return 0.0, "no-letters"

    has_brand = bool(_BRAND_RE.search(low))
    score = 0.0
    if has_brand:
        score += 0.5
    if _TYPE_RE.search(low):
        score += 0.3
    if _PARALLEL_RE.search(low):
        score += 0.3
    if _YEAR_RE.search(n):
        score += 0.2
    if n[0].isupper() or n[0].isdigit():
        score += 0.1

    # fragment penalties (only when there's no anchoring brand)
    if not has_brand:
        if n[0].islower():
            score -= 0.5
        if tokens[0].lower() in _LEADING_STOP:
            score -= 0.35
        # ends mid-thought
        if tokens[-1].lower() in {"the", "in", "for", "and", "of", "a", "an", "with", "to"}:
            score -= 0.3

    score = max(0.0, min(1.0, score))
    return score, ("brand" if has_brand else "no-brand")


def is_product_name(name: str, threshold: float = 0.5) -> bool:
    score, _ = product_name_quality(name)
    return score >= threshold
