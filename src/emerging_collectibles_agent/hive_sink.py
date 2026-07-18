"""Optional Hive sink (eBay / Krylov).

On every master-CSV write, mirror the consolidated intelligence into a Hive
table (default P_UK_PLAN_REPORT_T.foresight1) via PyHive + Kerberos.

Design:
- DISABLED by default (`hive_sink.enabled: false`) — it only runs on Krylov
  where PyHive + Kerberos are available. If pyhive is missing or the connection
  fails, it logs and returns; the pipeline never breaks.
- No secrets: the Kerberos principal comes from the KRYLOV_PRINCIPAL env var.
- Values are safely escaped and batched into multi-row INSERTs (row-by-row
  INSERT in Hive spawns one job per row — far too slow).
"""
from __future__ import annotations

import json
import os
from typing import Any

import pandas as pd

from .config import Config
from .logging_config import get_logger
from .util import utc_iso

log = get_logger("hive.sink")

# (column, hive_type) — mirrors the master CSV; numerics typed, everything else STRING.
HIVE_SCHEMA: list[tuple[str, str]] = [
    ("trending_date", "STRING"), ("focus_category", "STRING"), ("category", "STRING"),
    ("subcategory", "STRING"), ("product_name", "STRING"), ("brand", "STRING"),
    ("franchise", "STRING"), ("sport", "STRING"), ("player_or_character", "STRING"),
    ("product_type", "STRING"), ("set_name", "STRING"),
    ("eps", "DOUBLE"), ("vtm", "DOUBLE"), ("trend_confidence_pct", "DOUBLE"),
    ("confidence_level", "STRING"), ("final_trend_status", "STRING"),
    ("validation_status", "STRING"), ("matched_items", "INT"),
    ("total_source_count", "INT"), ("credible_source_count", "INT"),
    ("independent_domain_count", "INT"), ("why_trending", "STRING"),
    ("catalyst_summary", "STRING"), ("key_trend_signals", "STRING"),
    ("linked_event_name", "STRING"), ("linked_event_type", "STRING"),
    ("linked_event_date", "STRING"), ("linked_event_status", "STRING"),
    ("days_until_event", "INT"), ("days_since_event", "INT"),
    ("seasonality_score", "DOUBLE"), ("detected_country", "STRING"),
    ("detected_region", "STRING"), ("market_scope", "STRING"),
    ("global_signal_flag", "STRING"), ("scarcity_signal", "DOUBLE"),
    ("release_signal", "DOUBLE"), ("marketplace_signal", "DOUBLE"),
    ("source_types", "STRING"), ("source_domains", "STRING"),
    ("primary_source_url", "STRING"), ("source_urls", "STRING"),
    ("first_seen_at", "STRING"), ("last_seen_at", "STRING"),
    ("score_explanation", "STRING"), ("run_reference", "STRING"),
    ("loaded_at_utc", "STRING"),
]
_STR_LIMIT = 4000  # keep string literals sane


def _esc(v: Any) -> str:
    """Escape a value for a Hive string literal (or return NULL)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "NULL"
    s = str(v)
    if not s or s.lower() == "nan":
        return "NULL"
    s = s.replace("\\", "\\\\").replace("'", "''")
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    if len(s) > _STR_LIMIT:
        s = s[:_STR_LIMIT]
    return "'" + s + "'"


def _num(v: Any, integer: bool = False) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "NULL"
    try:
        return str(int(float(v))) if integer else str(float(v))
    except Exception:
        return "NULL"


class HiveSink:
    def __init__(self, config: Config):
        hs = config.get("hive_sink", {})
        self.enabled = bool(hs.get("enabled", False))
        self.host = hs.get("host", "hermes.prod.vip.ebay.com")
        self.port = int(hs.get("port", 10000))
        self.auth = hs.get("auth", "KERBEROS")
        self.kerberos_service_name = hs.get("kerberos_service_name", "b_carmel")
        self.username = config.env(hs.get("username_env", "KRYLOV_PRINCIPAL"))
        self.database = hs.get("database", "P_UK_PLAN_REPORT_T")
        self.table = hs.get("table", "foresight1")
        self.write_mode = hs.get("write_mode", "replace")   # replace | append
        self.batch_size = int(hs.get("batch_size", 50))
        # Optional username/password auth via a credentials file (used when
        # Kerberos is unavailable — e.g. no KDC for the realm). If the file
        # exists and carries a password, we connect with LDAP/CUSTOM SASL PLAIN
        # instead of Kerberos. The file may also override host/port/db/table/auth.
        self.credentials_file = hs.get("credentials_file", "credentials.json")
        self._creds = self._load_credentials()
        if self._creds:
            # let the creds file centralize connection details if it wants to
            self.host = self._creds.get("host", self.host)
            self.port = int(self._creds.get("port", self.port))
            self.database = self._creds.get("database", self.database)
            self.table = self._creds.get("table", self.table)
            self.username = self._creds.get("username", self.username)

    def _load_credentials(self) -> dict | None:
        """Read credentials.json if present. Tolerant of common key names.

        Recognized keys: username|user|principal, password|passwd|pass|secret,
        and optionally host, port, database, table, auth. Returns None if no
        usable password is found (falls back to Kerberos).
        """
        path = self.credentials_file
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            log.warning("Hive sink: could not read %s (%s); falling back to Kerberos.", path, exc)
            return None
        user = data.get("username") or data.get("user") or data.get("principal")
        pw = data.get("password") or data.get("passwd") or data.get("pass") or data.get("secret")
        if not pw:
            log.warning("Hive sink: %s has no password field; falling back to Kerberos.", path)
            return None
        out = {"username": user, "password": pw}
        for k in ("host", "port", "database", "table", "auth"):
            if data.get(k):
                out[k] = data[k]
        return out

    @property
    def fqtn(self) -> str:
        return f"{self.database}.{self.table}"

    def _connect(self):
        import pyhive.hive  # lazy: only needed on Krylov
        if self._creds:
            # password (SASL PLAIN) auth — no Kerberos ticket needed
            auth = self._creds.get("auth", self.auth)
            if auth not in ("LDAP", "CUSTOM", "PLAIN", "NONE"):
                auth = "LDAP"
            log.info("Hive sink: connecting to %s:%s as %s via %s (credentials.json).",
                     self.host, self.port, self._creds.get("username"), auth)
            return pyhive.hive.connect(
                host=self.host, port=self.port, auth=auth,
                username=self._creds.get("username"), password=self._creds.get("password"))
        # default: Kerberos
        log.info("Hive sink: connecting to %s:%s via KERBEROS (principal %s).",
                 self.host, self.port, self.username)
        return pyhive.hive.connect(
            host=self.host, port=self.port, auth=self.auth,
            kerberos_service_name=self.kerberos_service_name, username=self.username)

    def _create_table_sql(self) -> str:
        cols = ",\n    ".join(f"{c} {t}" for c, t in HIVE_SCHEMA)
        return f"CREATE TABLE IF NOT EXISTS {self.fqtn} (\n    {cols}\n)"

    def _row_values(self, row: dict) -> str:
        parts = []
        for col, typ in HIVE_SCHEMA:
            if col == "loaded_at_utc":
                parts.append(_esc(utc_iso()))
                continue
            v = row.get(col)
            if typ == "DOUBLE":
                parts.append(_num(v))
            elif typ == "INT":
                parts.append(_num(v, integer=True))
            else:
                parts.append(_esc(v))
        return "(" + ", ".join(parts) + ")"

    def write_master(self, master_df: pd.DataFrame) -> bool:
        """Push the current master dataframe to Hive. Returns True on success."""
        if not self.enabled:
            return False
        if master_df is None or master_df.empty:
            log.info("Hive sink: master frame empty; nothing to write.")
            return False
        if not self._creds and not self.username:
            log.warning("Hive sink enabled but no credentials.json and KRYLOV_PRINCIPAL not set; skipping.")
            return False
        try:
            conn = self._connect()
        except Exception as exc:
            log.warning("Hive sink: connection failed (%s); skipping (pipeline unaffected).", exc)
            return False
        # master CSV uses EPS/VTM (upper) — align to the Hive schema names
        master_df = master_df.rename(columns={"EPS": "eps", "VTM": "vtm"})
        try:
            cur = conn.cursor()
            cur.execute(self._create_table_sql())
            if self.write_mode == "replace":
                try:
                    cur.execute(f"TRUNCATE TABLE {self.fqtn}")
                except Exception as exc:
                    log.warning("Hive sink: TRUNCATE failed (%s); appending instead.", exc)
            cols = ", ".join(c for c, _ in HIVE_SCHEMA)
            rows = master_df.to_dict("records")
            written = 0
            for start in range(0, len(rows), self.batch_size):
                chunk = rows[start:start + self.batch_size]
                values = ",\n".join(self._row_values(r) for r in chunk)
                cur.execute(f"INSERT INTO {self.fqtn} ({cols}) VALUES\n{values}")
                written += len(chunk)
            try:
                conn.commit()
            except Exception:
                pass
            log.info("Hive sink: wrote %d rows to %s (mode=%s).", written, self.fqtn, self.write_mode)
            return True
        except Exception as exc:
            log.warning("Hive sink: write failed (%s); skipping (pipeline unaffected).", exc)
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass
