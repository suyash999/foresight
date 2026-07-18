"""LLM client wrapper.

- Optional: if disabled or no key, `available` is False and all callers fall
  back to deterministic logic. The system never crashes without an LLM.
- OpenAI-compatible: works with any base URL exposing /chat/completions.
- Structured: returns validated Pydantic models; on JSON failure it retries
  once, attempts a safe repair, then gives up (caller falls back).
"""
from __future__ import annotations

from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from ..config import Config
from ..logging_config import get_logger
from ..util import validate_model
from .json_repair import extract_json

log = get_logger("llm.client")
T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self, config: Config):
        self.config = config
        self.enabled = bool(config.get("llm.use_llm", False))
        self.model = config.env(config.get("llm.model_env", "LLM_MODEL"), "gpt-4o-mini")
        self.api_key = config.env(config.get("llm.api_key_env", "LLM_API_KEY"))
        self.base_url = config.env(config.get("llm.base_url_env", "LLM_BASE_URL"),
                                   "https://api.openai.com/v1")
        self.max_retries = int(config.get("llm.max_retries", 1))
        self.timeout = int(config.get("llm.timeout_seconds", 30))
        self.temperature = float(config.get("llm.temperature", 0.1))
        self._client = None
        if self.enabled and self.api_key:
            self._init_client()

    def _init_client(self) -> None:
        try:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url,
                                  timeout=self.timeout)
            log.info("LLM enabled: model=%s base_url=%s", self.model, self.base_url)
        except Exception as exc:  # pragma: no cover - import/optional
            log.warning("LLM requested but unavailable (%s); using deterministic fallback", exc)
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete_json(self, system: str, user: str) -> Optional[dict]:
        """Return parsed JSON dict or None (caller falls back)."""
        if not self.available:
            return None
        attempts = self.max_retries + 1
        for i in range(attempts):
            try:
                resp = self._client.chat.completions.create(  # type: ignore[union-attr]
                    model=self.model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=self.temperature,
                )
                content = resp.choices[0].message.content or ""
                parsed = extract_json(content)
                if parsed is not None:
                    return parsed
                log.warning("LLM JSON parse failed (attempt %d/%d)", i + 1, attempts)
            except Exception as exc:
                log.warning("LLM call failed (attempt %d/%d): %s", i + 1, attempts, exc)
        return None

    def complete_model(self, system: str, user: str, model_cls: Type[T]) -> Optional[T]:
        data = self.complete_json(system, user)
        if data is None:
            return None
        try:
            return validate_model(model_cls, data)
        except Exception as exc:
            log.warning("LLM output failed schema validation for %s: %s",
                        model_cls.__name__, exc)
            return None
