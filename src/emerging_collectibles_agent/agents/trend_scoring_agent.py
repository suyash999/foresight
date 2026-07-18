"""Trend Scoring Agent.

Computes EPS and VTM (transparent, config-weighted), links each product to the
best-supported real-world event, and assigns a final trend status. Also emits
per-component breakdowns and a plain-language score explanation.
"""
from __future__ import annotations

from typing import Optional

from dateutil import parser as dateparser

from ..config import Config
from ..logging_config import get_logger
from ..models import EPSBreakdown, EventRecord, VTMBreakdown
from ..util import dump_model
from .product_normalization_agent import NormalizedProduct
from ..scoring import classify_trend_status, confidence_from_scores
from ..scoring.credibility import evidence_quality_score
from ..scoring.eps import (compute_eps, event_proximity_score, freshness_score,
                           mention_velocity_score, source_diversity_score)
from ..scoring.vtm import (compute_vtm, credible_source_coverage,
                           independent_confirmation, mention_acceleration,
                           noise_penalty_adjusted)
from ..taxonomy import detect_signals
from ..util import hours_since, canonicalize_name

log = get_logger("agent.trendscore")

_NEWS_SENSITIVE = {"Sports Memorabilia", "Entertainment Memorabilia", "Autographs"}
_LONG_CYCLE = {"Watches", "Coins", "Vinyl Records", "Comic Books and Memorabilia"}


class TrendScoringAgent:
    def __init__(self, config: Config):
        self.config = config
        self.eps_weights = config.get("eps_weights", {})
        self.vtm_weights = config.get("vtm_weights", {})
        sp = config.get("scoring_params", {})
        self.hl = sp.get("freshness_half_life_hours", {})
        self.recent_window = float(sp.get("recent_window_hours", 48))
        self.baseline_window = float(sp.get("baseline_window_hours", 168))
        self.smoothing = float(sp.get("smoothing_factor", 1.0))
        self.target_source_types = int(sp.get("target_source_type_count", 4))
        self.required_credible = int(sp.get("required_credible_sources", 3))
        self.baselines = config.get("source_credibility_baselines", {})
        self.thresholds = config.get("validation_thresholds", {})
        self.credible_min = 0.6  # a source counts as "credible" above this

    def _half_life(self, vertical: str) -> float:
        if vertical == "Trading Cards":
            return float(self.hl.get("trading_cards", 72))
        if vertical in _LONG_CYCLE:
            return float(self.hl.get("long_cycle", 168))
        return float(self.hl.get("news_sensitive", 48))

    # -- event linkage ------------------------------------------------------
    def link_event(self, product: NormalizedProduct,
                   events: list[EventRecord]) -> tuple[Optional[EventRecord], dict]:
        pname = canonicalize_name(product.rep.product_name)
        ptokens = set(pname.split())
        vertical = product.rep.vertical
        best: Optional[EventRecord] = None
        best_score = 0.0
        for ev in events:
            score = 0.0
            if vertical and vertical in (ev.affected_verticals or []):
                score += 0.4
            etokens = set(canonicalize_name(ev.event_name).split())
            overlap = len(ptokens & etokens)
            score += min(0.4, 0.13 * overlap)
            # entity / brand overlap
            if product.rep.brand and product.rep.brand.lower() in ev.event_name.lower():
                score += 0.2
            if product.rep.player_or_character and product.rep.player_or_character.lower() in ev.event_name.lower():
                score += 0.2
            score *= (0.5 + 0.5 * ev.event_confidence_score)
            if score > best_score:
                best_score = score
                best = ev
        if best is None or best_score < 0.25:
            return None, {
                "linked_event_name": "", "linked_event_type": "",
                "linked_event_date": "", "linked_event_status": "not_found",
                "event_source_url": "", "event_to_product_reason": "No reliable linked catalyst found yet.",
                "event_confidence_score": 0.0, "days_until_event": None,
                "days_since_event": None, "event_proximity_score": 0.0,
                "seasonality_score": 0.0,
            }
        prox_days = best.days_until_event if best.days_until_event is not None else best.days_since_event
        half_life = 30.0 if best.event_type in ("cultural trend", "product release") else 21.0
        proximity = event_proximity_score(prox_days, half_life) if prox_days is not None else 0.3
        info = {
            "linked_event_name": best.event_name,
            "linked_event_type": best.event_type,
            "linked_event_date": best.event_date,
            "linked_event_status": best.event_status,
            "event_source_url": best.event_source_url,
            "event_to_product_reason": (
                f"Linked to '{best.event_name}' ({best.event_type}); {best.expected_product_impact_reason}"
            ),
            "event_confidence_score": round(min(1.0, best_score), 3),
            "days_until_event": best.days_until_event,
            "days_since_event": best.days_since_event,
            "event_proximity_score": round(proximity, 3),
            "seasonality_score": best.seasonality_score,
        }
        return best, info

    # -- aggregate candidate signals ----------------------------------------
    def _aggregate_signals(self, product: NormalizedProduct) -> dict:
        agg = {"scarcity": 0.0, "marketplace": 0.0, "release": 0.0, "news": 0.0,
               "has_price": False, "has_release": False, "has_scarcity": False,
               "has_auction": False, "has_official": False, "has_snippet": False,
               "has_direct_product_page": False}
        for c in product.candidates:
            s = detect_signals(f"{c.product_name} {c.evidence_snippet} {c.scarcity_language} "
                               f"{c.auction_or_sales_signal} {c.news_signal}")
            agg["scarcity"] = max(agg["scarcity"], s["scarcity_score"])
            agg["marketplace"] = max(agg["marketplace"], s["marketplace_score"])
            agg["release"] = max(agg["release"], s["release_score"])
            agg["news"] = max(agg["news"], s["news_catalyst_score"])
            if c.price is not None:
                agg["has_price"] = True
            if c.release_date or c.release_year:
                agg["has_release"] = True
            if c.scarcity_language:
                agg["has_scarcity"] = True
            if c.auction_or_sales_signal:
                agg["has_auction"] = True
            if c.extraction_method == "jsonld":
                agg["has_direct_product_page"] = True
            if c.evidence_snippet:
                agg["has_snippet"] = True
        return agg

    # -- main scoring -------------------------------------------------------
    def score(self, product: NormalizedProduct, events: list[EventRecord],
              baseline_mentions: float = 0.0,
              news_catalyst: float = 0.0) -> dict:
        rep = product.rep
        vertical = rep.vertical or "Collectibles"
        agg = self._aggregate_signals(product)
        _, event_info = self.link_event(product, events)

        # ---- EPS components (0..1) ----
        last_seen_dt = None
        try:
            last_seen_dt = dateparser.parse(product.last_seen)
        except Exception:
            pass
        fresh = freshness_score(hours_since(last_seen_dt), self._half_life(vertical))

        cred_scores = [self.baselines.get(st, 0.4) for st in product.source_types] or [0.4]
        source_cred = sum(cred_scores) / len(cred_scores)

        recent_mentions = float(product.mention_count)
        velocity = mention_velocity_score(recent_mentions, baseline_mentions, self.smoothing)
        diversity = source_diversity_score(len(product.source_types), self.target_source_types)
        news_score = max(agg["news"], news_catalyst)
        event_prox = event_info["event_proximity_score"]
        ai_conf = min(1.0, rep.extraction_confidence)

        eps_components = {
            "freshness": fresh,
            "source_credibility": source_cred,
            "mention_velocity": velocity,
            "source_diversity": diversity,
            "news_catalyst": news_score,
            "event_proximity": event_prox,
            "marketplace_signal": agg["marketplace"],
            "scarcity_signal": agg["scarcity"],
            "release_signal": agg["release"],
            "ai_confidence": ai_conf,
        }
        eps_b: EPSBreakdown = compute_eps(eps_components, self.eps_weights)

        # ---- VTM components (0..1) ----
        credible_count = sum(1 for st in product.source_types
                             if self.baselines.get(st, 0.4) >= self.credible_min)
        total_domains = max(1, len(product.source_domains))
        independent_domains = len(set(product.source_domains))
        csc = credible_source_coverage(credible_count, self.required_credible)
        indep = independent_confirmation(independent_domains, total_domains)
        recent_rate = recent_mentions / max(1.0, self.recent_window)
        baseline_rate = baseline_mentions / max(1.0, self.baseline_window)
        accel = mention_acceleration(recent_rate, baseline_rate, self.smoothing / self.baseline_window)
        evidence = evidence_quality_score({
            "has_product_name": True,
            "has_release_date": agg["has_release"],
            "has_price": agg["has_price"],
            "has_auction_result": agg["has_auction"],
            "has_marketplace_rank": agg["marketplace"] > 0.3,
            "has_official_announcement": any(st == "official manufacturer" for st in product.source_types),
            "has_scarcity_signal": agg["has_scarcity"],
            "has_direct_product_page": agg["has_direct_product_page"],
            "has_snippet": agg["has_snippet"],
        })
        event_validation = self._event_validation(event_info, product)
        cross_cat = 1.0 if event_info["linked_event_status"] not in ("not_found",) else (
            0.5 if news_score > 0 else 0.0)
        recency_consistency = min(1.0, independent_domains / 3.0) * fresh

        penalties = {}
        if product.source_count == 1:
            penalties["single_source_hype"] = 0.25
        if any(self.baselines.get(st, 0.4) < 0.35 for st in product.source_types) and product.source_count <= 1:
            penalties["spammy_source"] = 0.15
        if fresh < 0.15:
            penalties["old_article"] = 0.15
        if not rep.vertical:
            penalties["vague_mention"] = 0.15
        noise = noise_penalty_adjusted(penalties)

        vtm_components = {
            "credible_source_coverage": csc,
            "independent_confirmation": indep,
            "mention_acceleration": accel,
            "evidence_quality": evidence,
            "event_validation": event_validation,
            "cross_category_catalyst": cross_cat,
            "recency_consistency": recency_consistency,
            "noise_penalty_adjusted": noise,
        }
        vtm_b: VTMBreakdown = compute_vtm(vtm_components, self.vtm_weights)

        eps = eps_b.EPS
        vtm = vtm_b.VTM
        status = classify_trend_status(eps, vtm, self.thresholds)
        confidence = confidence_from_scores(eps, vtm)
        explanation = (
            f"EPS {eps} (freshness {fresh:.2f}, velocity {velocity:.2f}, diversity {diversity:.2f}, "
            f"news {news_score:.2f}, event {event_prox:.2f}); "
            f"VTM {vtm} (credible coverage {csc:.2f}, independence {indep:.2f}, evidence {evidence:.2f}, "
            f"event-validation {event_validation:.2f}, noise {noise:.2f}). Status: {status}."
        )

        return {
            "eps_breakdown": dump_model(eps_b),
            "vtm_breakdown": dump_model(vtm_b),
            "EPS": eps, "VTM": vtm,
            "final_trend_status": status,
            "confidence_level": confidence,
            "score_explanation": explanation,
            "credible_source_count": credible_count,
            "total_source_count": product.source_count,
            "independent_domain_count": independent_domains,
            "recent_mention_count": int(recent_mentions),
            "baseline_mention_count": int(baseline_mentions),
            "event": event_info,
            "aggregate_signals": agg,
        }

    def _event_validation(self, event_info: dict, product: NormalizedProduct) -> float:
        if event_info["linked_event_status"] == "not_found":
            return 0.0
        score = 0.0
        if event_info["linked_event_name"]:
            score += 0.3
        if event_info["event_source_url"]:
            score += 0.2
        if event_info["linked_event_date"]:
            score += 0.2
        score += 0.3 * event_info["event_confidence_score"]
        # multiple sources supporting
        if product.source_count >= 2:
            score = min(1.0, score + 0.1)
        return min(1.0, score)
