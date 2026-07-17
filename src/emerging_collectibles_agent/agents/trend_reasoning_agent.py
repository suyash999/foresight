"""Trend Reasoning Agent.

Generates an evidence-backed explanation of why a product is emerging. Fully
deterministic by default (never fabricates sources/events); optional LLM
polish. Every claim traces to counted sources, detected signals, and linked
events — no hallucinated evidence.
"""
from __future__ import annotations

from typing import Optional

from ..config import Config
from ..llm.client import LLMClient
from ..llm.prompts import TREND_REASONING_SYSTEM, TREND_REASONING_USER
from ..logging_config import get_logger
from ..models import TrendReason
from .product_normalization_agent import NormalizedProduct

log = get_logger("agent.reason")


class TrendReasoningAgent:
    def __init__(self, config: Config, llm: Optional[LLMClient] = None):
        self.config = config
        self.llm = llm

    def reason(self, product: NormalizedProduct, scored: dict) -> TrendReason:
        rep = product.rep
        eps = scored["EPS"]
        vtm = scored["VTM"]
        agg = scored["aggregate_signals"]
        event = scored["event"]

        signals: list[str] = []
        if scored["independent_domain_count"] > 1:
            signals.append(f"{scored['independent_domain_count']} independent domains")
        signals.append(f"{product.source_count} source(s) across {len(product.source_types)} source type(s)")
        if agg["scarcity"] > 0:
            signals.append("scarcity/sold-out language")
        if agg["marketplace"] > 0:
            signals.append("marketplace/auction activity")
        if agg["release"] > 0:
            signals.append("release/preorder timing")
        if agg["news"] > 0:
            signals.append("news catalyst")
        if event["linked_event_status"] not in ("not_found", ""):
            signals.append(f"linked event: {event['linked_event_name']} ({event['linked_event_status']})")

        catalyst = (event["event_to_product_reason"] if event["linked_event_status"] != "not_found"
                    else "No confirmed external catalyst yet.")

        source_types = ", ".join(product.source_types) or "unclassified"
        evidence_summary = (
            f"Mentioned by {product.source_count} external source(s) "
            f"({source_types}); {product.mention_count} total mention(s). "
            f"Representative evidence: \"{rep.evidence_snippet[:160]}\""
        )

        counter: list[str] = []
        if product.source_count == 1:
            counter.append("Single-source signal — needs independent confirmation.")
        if agg["scarcity"] == 0 and agg["marketplace"] == 0:
            counter.append("No scarcity or marketplace signal detected yet.")
        if event["linked_event_status"] == "not_found":
            counter.append("No linked real-world catalyst found.")
        if scored["baseline_mention_count"] == 0 and product.mention_count <= 1:
            counter.append("No historical baseline — velocity may be first-sighting noise.")

        reason_text = (
            f"'{rep.product_name}' appears {scored['final_trend_status'].lower()} "
            f"(EPS {eps}, VTM {vtm}). It was surfaced by {product.source_count} external "
            f"source(s) spanning {len(product.source_types)} source type(s)"
            + (f", supported by {catalyst.lower()}" if event["linked_event_status"] != "not_found" else "")
            + ". Signals: " + (", ".join(signals) if signals else "limited") + "."
        )

        tr = TrendReason(
            ai_trend_reason=reason_text,
            key_trend_signals=signals,
            evidence_summary=evidence_summary,
            source_summary=source_types,
            catalyst_summary=catalyst,
            counter_signals=counter,
            confidence_level=scored["confidence_level"],
            recommended_next_validation=self._recommend(product, scored),
        )

        if self.llm and self.llm.available:
            self._llm_polish(tr, product, scored)
        return tr

    def _recommend(self, product: NormalizedProduct, scored: dict) -> str:
        if product.source_count < 2:
            return "Find at least one more independent credible source before acting."
        if scored["event"]["linked_event_status"] == "not_found":
            return "Confirm a linked catalyst (release date, auction result, or news event)."
        if scored["VTM"] < 40:
            return "Seek marketplace/auction confirmation to raise validation."
        return "Monitor mention velocity and marketplace pricing over the next cycles."

    def _llm_polish(self, tr: TrendReason, product: NormalizedProduct, scored: dict) -> None:
        try:
            snippets = " | ".join(c.evidence_snippet[:120] for c in product.candidates[:3])
            user = TREND_REASONING_USER.format(
                product_name=product.rep.product_name, eps=scored["EPS"], vtm=scored["VTM"],
                source_count=product.source_count, source_types=", ".join(product.source_types),
                catalyst=tr.catalyst_summary, scarcity=product.rep.scarcity_language,
                snippets=snippets)
            data = self.llm.complete_json(TREND_REASONING_SYSTEM, user)
        except Exception:
            return
        if not data:
            return
        # only override text fields; keep deterministic counts intact
        if data.get("ai_trend_reason"):
            tr.ai_trend_reason = data["ai_trend_reason"]
        if data.get("key_trend_signals"):
            tr.key_trend_signals = data["key_trend_signals"]
        if data.get("counter_signals"):
            tr.counter_signals = data["counter_signals"]
        if data.get("recommended_next_validation"):
            tr.recommended_next_validation = data["recommended_next_validation"]
