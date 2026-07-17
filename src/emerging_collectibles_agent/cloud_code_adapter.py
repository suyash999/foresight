"""Optional Cloud Code / Kryo API adapter (DISABLED by default).

Governance-first design:
  - Controlled entirely by `cloud_code.use_cloud_code_api` in config (default false).
  - No internal data leaves the process unless `cloud_code.send_internal_data`
    is explicitly true AND the adapter is enabled.
  - No secrets are hardcoded; keys are read from environment variables named in
    config. If unavailable, the system runs fully locally and this adapter is a
    no-op.

This is a clean interface only — it never runs unless a governance reviewer
turns it on. See README "Optional Kryo / Cloud Code API adapter notes".
"""
from __future__ import annotations

from typing import Any, Optional

from .config import Config
from .logging_config import get_logger

log = get_logger("cloud_code")


class CloudCodeAdapter:
    def __init__(self, config: Config):
        cc = config.get("cloud_code", {})
        self.enabled = bool(cc.get("use_cloud_code_api", False))
        self.send_internal_data = bool(cc.get("send_internal_data", False))
        self.api_key = config.env(cc.get("api_key_env", "CLOUD_CODE_API_KEY"))
        self.base_url = config.env(cc.get("base_url_env", "CLOUD_CODE_BASE_URL"))
        if self.enabled and not self.api_key:
            log.warning("Cloud Code adapter enabled but no API key present; disabling.")
            self.enabled = False

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key) and bool(self.base_url)

    def enrich(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Send a payload for optional external enrichment.

        Returns None (no-op) unless the adapter is explicitly enabled and
        allowed to send data. Never sends internal data without opt-in.
        """
        if not self.available:
            return None
        if not self.send_internal_data:
            log.info("Cloud Code enabled but send_internal_data=false; skipping external call.")
            return None
        try:
            import httpx

            resp = httpx.post(f"{self.base_url.rstrip('/')}/enrich",
                              headers={"Authorization": f"Bearer {self.api_key}"},
                              json=payload, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            log.warning("Cloud Code returned %s", resp.status_code)
        except Exception as exc:
            log.warning("Cloud Code call failed (running locally instead): %s", exc)
        return None
