"""Small shared utilities: time (UTC + IST), hashing, URL/domain helpers."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import pytz

try:
    import tldextract

    # suffix_list_urls=() forces tldextract to use its bundled snapshot and NEVER
    # hit the network. On locked-down/proxied hosts the live fetch to
    # publicsuffix.org otherwise throws a noisy (caught) 407/proxy traceback.
    _TLD = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())
except Exception:  # pragma: no cover
    _TLD = None

IST = pytz.timezone("Asia/Kolkata")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime | None = None) -> str:
    dt = dt or now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def dump_model(m):
    """Export a Pydantic model to a dict on either Pydantic v1 or v2.

    Krylov's conda base ships Pydantic v1 (no ``model_dump``); local/dev envs
    have v2 (no ``.dict()`` deprecation). This bridges both.
    """
    if hasattr(m, "model_dump"):
        return m.model_dump()
    return m.dict()


def validate_model(model_cls, data):
    """Construct a Pydantic model from a dict on either Pydantic v1 or v2."""
    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(data)
    return model_cls.parse_obj(data)


def to_ist_iso(dt: datetime | None = None, tz_name: str = "Asia/Kolkata") -> str:
    dt = dt or now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = IST
    return dt.astimezone(tz).isoformat()


def hours_since(dt: datetime | None) -> float:
    if dt is None:
        return 1e6
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now_utc() - dt).total_seconds() / 3600.0)


def sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "ignore")).hexdigest()


def registered_domain(url: str) -> str:
    """Return the registrable domain (example.co.uk) lower-cased."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if not host:
        # maybe a bare domain was passed
        host = url.lower().strip().strip("/")
    host = host.split("@")[-1].split(":")[0]
    if _TLD is not None:
        ext = _TLD(host)
        if ext.domain and ext.suffix:
            return f"{ext.domain}.{ext.suffix}"
        return host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def full_host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""


def normalize_url(url: str, base: str | None = None) -> str:
    """Resolve relative URLs, drop fragments, strip tracking params."""
    if not url:
        return ""
    url = url.strip()
    if base:
        url = urljoin(base, url)
    url, _ = urldefrag(url)
    # strip common tracking params
    if "?" in url:
        base_part, _, query = url.partition("?")
        keep = []
        for kv in query.split("&"):
            key = kv.split("=")[0].lower()
            if key.startswith("utm_") or key in {"fbclid", "gclid", "ref", "mc_cid", "mc_eid"}:
                continue
            if kv:
                keep.append(kv)
        url = base_part + ("?" + "&".join(keep) if keep else "")
    return url.rstrip("/") if url.endswith("/") and url.count("/") > 3 else url


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def canonicalize_name(name: str) -> str:
    """Normalise a product name for dedupe: lowercase, strip punctuation,
    collapse whitespace, sort-independent token form kept readable."""
    if not name:
        return ""
    text = name.lower().strip()
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return text


# Generic filler words dropped from the dedupe key so wording variants of the
# SAME product collapse together (salient tokens — year/brand/set/player/parallel
# — are kept).
_KEY_STOPWORDS = {
    "the", "a", "an", "and", "of", "for", "with", "new", "full", "review",
    "checklist", "context", "these", "this", "that", "other", "per", "are",
    "is", "was", "were", "have", "has", "series", "history", "here", "at",
    "to", "in", "on", "by", "from", "card", "cards", "set", "sets", "edition",
    "including", "include", "featuring", "official", "guide", "list", "info",
}


def canonical_key(name: str) -> str:
    """A strong dedupe key: salient, order-independent tokens with generic
    filler removed so wording variants of the same product collapse."""
    canon = canonicalize_name(name)
    tokens = [t for t in canon.split() if len(t) > 1 and t not in _KEY_STOPWORDS]
    if not tokens:  # fall back to raw tokens if everything was filler
        tokens = [t for t in canon.split() if len(t) > 1]
    return " ".join(sorted(set(tokens)))


def clean_text(text: str | None, limit: int | None = None) -> str:
    if not text:
        return ""
    out = _WS.sub(" ", text).strip()
    if limit and len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0] + "…"
    return out
