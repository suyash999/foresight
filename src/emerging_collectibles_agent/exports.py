"""Dataframe assembly and multi-format persistence.

Builds the wide product dataframe (the system's primary output) and writes:
  outputs/products_latest.csv / .parquet / .json
  outputs/source_scores_latest.csv
  outputs/url_crawl_log_latest.csv
  outputs/news_signal_latest.csv
"""
from __future__ import annotations

import json
import os
from typing import Any

import pandas as pd

from .agents.product_normalization_agent import NormalizedProduct
from .db import Database
from .logging_config import get_logger
from .models import TrendReason
from .util import sha1, to_ist_iso, utc_iso

log = get_logger("exports")

# Column order for the main dataframe (matches the spec schema).
PRODUCT_COLUMNS = [
    "run_id", "run_reference", "observed_at_utc", "observed_at_ist",
    "product_id", "product_name", "canonical_product_name",
    "vertical", "category", "subcategory", "brand", "franchise", "sport", "team",
    "player_or_character", "product_line", "product_type", "set_name",
    "release_date", "release_year", "price", "currency",
    "source_urls", "primary_source_url", "source_domains", "source_types",
    "source_credibility_avg", "credible_source_count", "total_source_count",
    "independent_domain_count", "mention_count", "recent_mention_count",
    "baseline_mention_count",
    "freshness_score", "source_credibility_score", "mention_velocity_score",
    "source_diversity_score", "news_catalyst_score", "event_proximity_score",
    "marketplace_signal_score", "scarcity_signal_score", "release_signal_score",
    "ai_confidence_score", "EPS",
    "credible_source_coverage", "independent_confirmation", "mention_acceleration",
    "evidence_quality", "event_validation_score", "cross_category_catalyst",
    "recency_consistency", "noise_penalty_adjusted", "VTM",
    "final_trend_status", "validation_status", "confidence_level",
    "key_trend_signals", "evidence_summary", "evidence_snippets",
    "catalyst_summary", "ai_trend_reason", "counter_signals",
    "recommended_next_validation", "trending_date", "first_seen_at", "last_seen_at",
    "extraction_method", "extraction_confidence", "raw_context_hash",
    # geography
    "detected_country", "detected_region", "source_country", "market_scope",
    "global_signal_flag", "local_signal_flag", "geo_confidence",
    # event linkage
    "linked_event_name", "linked_event_type", "linked_event_date",
    "linked_event_status", "event_source_url", "event_to_product_reason",
    "event_confidence_score", "days_until_event", "days_since_event",
    "seasonality_score",
    # trading-card optional
    "card_set", "card_number", "rookie_card_flag", "autograph_flag",
    "memorabilia_flag", "parallel_variant", "serial_numbered_flag", "print_run",
    "grading_company", "grade", "sealed_product_type", "hobby_box_flag",
    "blaster_box_flag", "booster_box_flag", "case_flag",
    # other collectible optional
    "coin_year", "coin_mint", "coin_grade", "comic_issue_number",
    "comic_publisher", "toy_brand", "watch_model", "watch_reference_number",
    "vinyl_artist", "vinyl_album", "limited_edition_flag",
    "score_explanation",
]


def build_product_row(run_id: str, run_reference: int, product: NormalizedProduct,
                      scored: dict, reason: TrendReason, validation_status: str) -> dict[str, Any]:
    rep = product.rep
    eps_b = scored["eps_breakdown"]
    vtm_b = scored["vtm_breakdown"]
    event = scored["event"]
    cred_scores = scored.get("_cred_scores", [])
    cred_avg = round(sum(cred_scores) / len(cred_scores), 3) if cred_scores else \
        round(eps_b["source_credibility_score"], 3)
    snippets = " || ".join(c.evidence_snippet for c in product.candidates[:5] if c.evidence_snippet)

    row = {
        "run_id": run_id,
        "run_reference": run_reference,
        "observed_at_utc": utc_iso(),
        "observed_at_ist": to_ist_iso(),
        "product_id": product.product_id,
        "product_name": rep.product_name,
        "canonical_product_name": product.canonical_product_name,
        "vertical": rep.vertical,
        "category": rep.category,
        "subcategory": rep.subcategory,
        "brand": rep.brand,
        "franchise": rep.franchise,
        "sport": rep.sport,
        "team": rep.team,
        "player_or_character": rep.player_or_character,
        "product_line": rep.product_line,
        "product_type": rep.product_type,
        "set_name": rep.set_name,
        "release_date": rep.release_date,
        "release_year": rep.release_year,
        "price": rep.price,
        "currency": rep.currency,
        "source_urls": " | ".join(product.source_urls),
        "primary_source_url": product.source_urls[0] if product.source_urls else "",
        "source_domains": " | ".join(product.source_domains),
        "source_types": " | ".join(product.source_types),
        "source_credibility_avg": cred_avg,
        "credible_source_count": scored["credible_source_count"],
        "total_source_count": scored["total_source_count"],
        "independent_domain_count": scored["independent_domain_count"],
        "mention_count": product.mention_count,
        "recent_mention_count": scored["recent_mention_count"],
        "baseline_mention_count": scored["baseline_mention_count"],
        "freshness_score": round(eps_b["freshness_score"], 3),
        "source_credibility_score": round(eps_b["source_credibility_score"], 3),
        "mention_velocity_score": round(eps_b["mention_velocity_score"], 3),
        "source_diversity_score": round(eps_b["source_diversity_score"], 3),
        "news_catalyst_score": round(eps_b["news_catalyst_score"], 3),
        "event_proximity_score": round(eps_b["event_proximity_score"], 3),
        "marketplace_signal_score": round(eps_b["marketplace_signal_score"], 3),
        "scarcity_signal_score": round(eps_b["scarcity_signal_score"], 3),
        "release_signal_score": round(eps_b["release_signal_score"], 3),
        "ai_confidence_score": round(eps_b["ai_confidence_score"], 3),
        "EPS": scored["EPS"],
        "credible_source_coverage": round(vtm_b["credible_source_coverage"], 3),
        "independent_confirmation": round(vtm_b["independent_confirmation"], 3),
        "mention_acceleration": round(vtm_b["mention_acceleration"], 3),
        "evidence_quality": round(vtm_b["evidence_quality"], 3),
        "event_validation_score": round(vtm_b["event_validation_score"], 3),
        "cross_category_catalyst": round(vtm_b["cross_category_catalyst"], 3),
        "recency_consistency": round(vtm_b["recency_consistency"], 3),
        "noise_penalty_adjusted": round(vtm_b["noise_penalty_adjusted"], 3),
        "VTM": scored["VTM"],
        "final_trend_status": scored["final_trend_status"],
        "validation_status": validation_status,
        "confidence_level": reason.confidence_level,
        "key_trend_signals": " | ".join(reason.key_trend_signals),
        "evidence_summary": reason.evidence_summary,
        "evidence_snippets": snippets,
        "catalyst_summary": reason.catalyst_summary,
        "ai_trend_reason": reason.ai_trend_reason,
        "counter_signals": " | ".join(reason.counter_signals),
        "recommended_next_validation": reason.recommended_next_validation,
        "trending_date": (product.last_seen or utc_iso())[:10],
        "first_seen_at": product.first_seen,
        "last_seen_at": product.last_seen,
        "extraction_method": rep.extraction_method,
        "extraction_confidence": rep.extraction_confidence,
        "raw_context_hash": sha1(rep.evidence_snippet + rep.product_name),
        "detected_country": rep.detected_country,
        "detected_region": rep.detected_region,
        "source_country": rep.source_country,
        "market_scope": rep.market_scope,
        "global_signal_flag": rep.global_signal_flag,
        "local_signal_flag": rep.local_signal_flag,
        "geo_confidence": rep.geo_confidence,
        "linked_event_name": event["linked_event_name"],
        "linked_event_type": event["linked_event_type"],
        "linked_event_date": event["linked_event_date"],
        "linked_event_status": event["linked_event_status"],
        "event_source_url": event["event_source_url"],
        "event_to_product_reason": event["event_to_product_reason"],
        "event_confidence_score": event["event_confidence_score"],
        "days_until_event": event["days_until_event"],
        "days_since_event": event["days_since_event"],
        "seasonality_score": event["seasonality_score"],
        "card_set": rep.card_set,
        "card_number": rep.card_number,
        "rookie_card_flag": rep.rookie_card_flag,
        "autograph_flag": rep.autograph_flag,
        "memorabilia_flag": rep.memorabilia_flag,
        "parallel_variant": rep.parallel_variant,
        "serial_numbered_flag": rep.serial_numbered_flag,
        "print_run": rep.print_run,
        "grading_company": rep.grading_company,
        "grade": rep.grade,
        "sealed_product_type": rep.sealed_product_type,
        "hobby_box_flag": rep.hobby_box_flag,
        "blaster_box_flag": rep.blaster_box_flag,
        "booster_box_flag": rep.booster_box_flag,
        "case_flag": rep.case_flag,
        "coin_year": rep.coin_year,
        "coin_mint": rep.coin_mint,
        "coin_grade": rep.coin_grade,
        "comic_issue_number": rep.comic_issue_number,
        "comic_publisher": rep.comic_publisher,
        "toy_brand": rep.toy_brand,
        "watch_model": rep.watch_model,
        "watch_reference_number": rep.watch_reference_number,
        "vinyl_artist": rep.vinyl_artist,
        "vinyl_album": rep.vinyl_album,
        "limited_edition_flag": rep.limited_edition_flag,
        "score_explanation": scored["score_explanation"],
    }
    return row


class Exporter:
    def __init__(self, config, db: Database):
        self.config = config
        self.db = db
        self.output_dir = config.output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def products_dataframe(self, rows: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS) if rows else pd.DataFrame(columns=PRODUCT_COLUMNS)
        return df

    def save_products(self, rows: list[dict], run_id: str) -> pd.DataFrame:
        df = self.products_dataframe(rows)
        csv_path = os.path.join(self.output_dir, "products_latest.csv")
        pq_path = os.path.join(self.output_dir, "products_latest.parquet")
        json_path = os.path.join(self.output_dir, "products_latest.json")
        df.to_csv(csv_path, index=False)
        try:
            df.to_parquet(pq_path, index=False)
        except Exception as exc:
            log.warning("Parquet write failed (%s); continuing with CSV/JSON", exc)
        df.to_json(json_path, orient="records", indent=2)

        # persist wide snapshot to sqlite
        for row in rows:
            self.db.upsert("product_dataframe", {
                "product_id": row["product_id"], "run_id": run_id,
                "payload_json": json.dumps(row, default=str),
                "eps": row["EPS"], "vtm": row["VTM"],
                "final_trend_status": row["final_trend_status"],
                "updated_at": utc_iso(),
            }, ["product_id"])
        log.info("Exported %d products to CSV/Parquet/JSON", len(rows))
        return df

    def save_aux(self) -> None:
        # source scores
        try:
            self.db.table_df("source_domains").to_csv(
                os.path.join(self.output_dir, "source_scores_latest.csv"), index=False)
        except Exception:
            pass
        try:
            self.db.table_df("crawl_urls").to_csv(
                os.path.join(self.output_dir, "url_crawl_log_latest.csv"), index=False)
        except Exception:
            pass
        try:
            self.db.table_df("news_signals").to_csv(
                os.path.join(self.output_dir, "news_signal_latest.csv"), index=False)
        except Exception:
            pass
