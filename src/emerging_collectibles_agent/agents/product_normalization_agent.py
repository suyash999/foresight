"""Product Normalization and Deduplication Agent.

Merges repeated mentions of the same product across sources using canonical
keys + fuzzy matching (rapidfuzz). Generates a stable product_id and merges
source URLs, domains, source types, and evidence.
"""
from __future__ import annotations

from typing import Optional

from ..config import Config
from ..logging_config import get_logger
from ..models import ProductCandidate
from ..util import canonical_key, canonicalize_name, now_utc, sha1

log = get_logger("agent.normalize")

try:
    from rapidfuzz import fuzz
    _HAVE_FUZZ = True
except Exception:  # pragma: no cover
    _HAVE_FUZZ = False


class NormalizedProduct:
    """Merged product aggregate consumed by scoring/reasoning."""

    def __init__(self, rep: ProductCandidate):
        self.rep = rep
        self.product_id = sha1(canonical_key(rep.product_name) or rep.product_name)[:16]
        self.canonical_product_name = canonicalize_name(rep.product_name)
        self.candidates: list[ProductCandidate] = [rep]
        self.source_urls: list[str] = []
        self.source_domains: list[str] = []
        self.source_types: list[str] = []
        self.observed_times: list[str] = []

    def add(self, pc: ProductCandidate):
        self.candidates.append(pc)

    def finalize(self, source_type_by_domain: dict[str, str]):
        urls, domains, stypes, times = [], [], [], []
        best_conf = -1.0
        for c in self.candidates:
            if c.source_url:
                urls.append(c.source_url)
            if c.source_domain:
                domains.append(c.source_domain)
                stypes.append(source_type_by_domain.get(c.source_domain, "unknown"))
            if c.observed_at_utc:
                times.append(c.observed_at_utc)
            if c.extraction_confidence > best_conf:
                best_conf = c.extraction_confidence
                self.rep = c  # keep highest-confidence representative
        self.source_urls = list(dict.fromkeys(urls))
        self.source_domains = list(dict.fromkeys(domains))
        self.source_types = list(dict.fromkeys(stypes))
        self.observed_times = sorted(times)

    @property
    def source_count(self) -> int:
        return len(self.source_domains)

    @property
    def mention_count(self) -> int:
        return len(self.candidates)

    @property
    def first_seen(self) -> str:
        return self.observed_times[0] if self.observed_times else now_utc().isoformat()

    @property
    def last_seen(self) -> str:
        return self.observed_times[-1] if self.observed_times else now_utc().isoformat()


class ProductNormalizationAgent:
    def __init__(self, config: Config, memory=None):
        self.config = config
        self.memory = memory
        self.fuzzy_threshold = 88

    def normalize(self, candidates: list[ProductCandidate],
                  source_type_by_domain: Optional[dict[str, str]] = None) -> list[NormalizedProduct]:
        source_type_by_domain = source_type_by_domain or {}
        groups: dict[str, NormalizedProduct] = {}

        for pc in candidates:
            key = canonical_key(pc.product_name)
            if not key:
                continue
            if key in groups:
                groups[key].add(pc)
                continue
            # fuzzy merge into an existing group?
            merged = False
            if _HAVE_FUZZ and pc.vertical:
                cname = canonicalize_name(pc.product_name)
                for gkey, grp in groups.items():
                    if grp.rep.vertical and grp.rep.vertical != pc.vertical:
                        continue
                    ratio = fuzz.token_set_ratio(cname, grp.canonical_product_name)
                    if ratio >= self.fuzzy_threshold:
                        grp.add(pc)
                        merged = True
                        break
            if not merged:
                groups[key] = NormalizedProduct(pc)

        result = list(groups.values())
        for grp in result:
            grp.finalize(source_type_by_domain)
        log.info("Normalized %d candidates → %d unique products", len(candidates), len(result))
        return result
