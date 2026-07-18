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
from .hive_sink import HiveSink
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
    # reference-style / focus-category fields
    "focus_category", "trend_confidence_pct", "matched_items",
]

# Consolidated, human-readable master export (SKU → focus-category precision,
# row by row: what is trending, why, the event, the market, and the sources).
MASTER_COLUMNS = [
    "run_reference", "trending_date", "focus_category", "category", "subcategory",
    "product_name", "brand", "franchise", "sport", "player_or_character",
    "product_type", "set_name",
    "EPS", "VTM", "trend_confidence_pct", "confidence_level",
    "final_trend_status", "validation_status",
    "matched_items", "total_source_count", "credible_source_count", "independent_domain_count",
    "why_trending", "catalyst_summary", "key_trend_signals",
    "linked_event_name", "linked_event_type", "linked_event_date", "linked_event_status",
    "days_until_event", "days_since_event", "seasonality_score",
    "detected_country", "detected_region", "market_scope", "global_signal_flag",
    "scarcity_signal", "release_signal", "marketplace_signal",
    "source_types", "source_domains", "primary_source_url", "source_urls",
    "first_seen_at", "last_seen_at", "score_explanation",
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
        "focus_category": rep.vertical,   # focus category == vertical
        "trend_confidence_pct": round((scored["EPS"] + scored["VTM"]) / 2.0),
        "matched_items": product.mention_count,
    }
    return row


def build_master_row(product_row: dict) -> dict[str, Any]:
    """Flatten a full product row into the consolidated master schema."""
    return {
        "run_reference": product_row.get("run_reference"),
        "trending_date": product_row.get("trending_date"),
        "focus_category": product_row.get("focus_category") or product_row.get("vertical"),
        "category": product_row.get("category"),
        "subcategory": product_row.get("subcategory"),
        "product_name": product_row.get("product_name"),
        "brand": product_row.get("brand"),
        "franchise": product_row.get("franchise"),
        "sport": product_row.get("sport"),
        "player_or_character": product_row.get("player_or_character"),
        "product_type": product_row.get("product_type"),
        "set_name": product_row.get("set_name"),
        "EPS": product_row.get("EPS"),
        "VTM": product_row.get("VTM"),
        "trend_confidence_pct": product_row.get("trend_confidence_pct"),
        "confidence_level": product_row.get("confidence_level"),
        "final_trend_status": product_row.get("final_trend_status"),
        "validation_status": product_row.get("validation_status"),
        "matched_items": product_row.get("matched_items"),
        "total_source_count": product_row.get("total_source_count"),
        "credible_source_count": product_row.get("credible_source_count"),
        "independent_domain_count": product_row.get("independent_domain_count"),
        "why_trending": product_row.get("ai_trend_reason"),
        "catalyst_summary": product_row.get("catalyst_summary"),
        "key_trend_signals": product_row.get("key_trend_signals"),
        "linked_event_name": product_row.get("linked_event_name"),
        "linked_event_type": product_row.get("linked_event_type"),
        "linked_event_date": product_row.get("linked_event_date"),
        "linked_event_status": product_row.get("linked_event_status"),
        "days_until_event": product_row.get("days_until_event"),
        "days_since_event": product_row.get("days_since_event"),
        "seasonality_score": product_row.get("seasonality_score"),
        "detected_country": product_row.get("detected_country"),
        "detected_region": product_row.get("detected_region"),
        "market_scope": product_row.get("market_scope"),
        "global_signal_flag": product_row.get("global_signal_flag"),
        "scarcity_signal": product_row.get("scarcity_signal_score"),
        "release_signal": product_row.get("release_signal_score"),
        "marketplace_signal": product_row.get("marketplace_signal_score"),
        "source_types": product_row.get("source_types"),
        "source_domains": product_row.get("source_domains"),
        "primary_source_url": product_row.get("primary_source_url"),
        "source_urls": product_row.get("source_urls"),
        "first_seen_at": product_row.get("first_seen_at"),
        "last_seen_at": product_row.get("last_seen_at"),
        "score_explanation": product_row.get("score_explanation"),
    }


class Exporter:
    def __init__(self, config, db: Database):
        self.config = config
        self.db = db
        self.output_dir = config.output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.hive = HiveSink(config)
        self.clear_csv_after_write = bool(config.get("hive_sink.clear_csv_after_write", True))

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
        # consolidated master CSV — everything in one file, human-readable
        master_rows = [build_master_row(r) for r in rows]
        master_df = pd.DataFrame(master_rows, columns=MASTER_COLUMNS) if master_rows \
            else pd.DataFrame(columns=MASTER_COLUMNS)
        master_df = master_df.sort_values("EPS", ascending=False) if not master_df.empty else master_df
        master_path = os.path.join(self.output_dir, "master_intelligence_latest.csv")
        master_df.to_csv(master_path, index=False)
        # Local accumulating copy that MIRRORS the Hermes append — one durable copy
        # on disk, one in Hermes. Header written once; each cycle appends its rows.
        if not master_df.empty:
            hist_path = os.path.join(self.output_dir, "master_intelligence_history.csv")
            master_df.to_csv(hist_path, mode="a", index=False,
                             header=not os.path.exists(hist_path))
        log.info("Exported %d products to CSV/Parquet/JSON + master CSV (+ history)", len(rows))

        # Hive sink (Krylov). write_mode=append => create table if absent, then
        # append this cycle's rows. Local CSVs are kept (clear_csv_after_write=false)
        # so there is always one copy on disk and one in Hermes.
        try:
            hive_ok = self.hive.write_master(master_df)
            if hive_ok and self.clear_csv_after_write:
                pd.DataFrame(columns=MASTER_COLUMNS).to_csv(master_path, index=False)
                log.info("Master CSV flushed to Hive and cleared to header-only.")
        except Exception as exc:  # never let the sink break the pipeline
            log.warning("Hive sink step failed (%s); master CSV retained.", exc)
        return df

    def save_aux(self) -> None:
        # full exhaustive URL discovery list (every URL + why selected/rejected)
        try:
            self.db.table_df("url_discovery_log").to_csv(
                os.path.join(self.output_dir, "url_discovery_latest.csv"), index=False)
        except Exception:
            pass
        # event calendar (curated + discovered) with SKU-level product hints
        try:
            self.db.table_df("event_calendar").to_csv(
                os.path.join(self.output_dir, "event_calendar_latest.csv"), index=False)
        except Exception:
            pass
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
