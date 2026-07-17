"""Best-effort JSON extraction/repair for LLM outputs.

If parsing fails after a safe repair, callers fall back to deterministic logic
rather than crashing.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional


def extract_json(text: str) -> Optional[Any]:
    if not text:
        return None
    text = text.strip()
    # strip code fences
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # grab the first {...} or [...] block
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        candidate = match.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            return _safe_repair(candidate)
    return None


def _safe_repair(candidate: str) -> Optional[Any]:
    repaired = candidate
    # remove trailing commas
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    # normalise smart quotes
    repaired = repaired.replace("“", '"').replace("”", '"').replace("’", "'")
    # single -> double quotes for keys (best effort, conservative)
    try:
        return json.loads(repaired)
    except Exception:
        return None
