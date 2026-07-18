"""SQLite persistence layer.

Thread-safe (a single connection guarded by a lock) so the background worker
and any readers can share it. Provides small generic helpers plus a few
upsert helpers used by the self-learning memory tables.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Iterable, Optional

import pandas as pd

from .storage.migrations import migrate


class Database:
    def __init__(self, path: str, journal_mode: str = "DELETE"):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        # WAL uses a memory-mapped -shm file, which SIGBUS/corrupts on network
        # filesystems (Krylov mounts the workspace over NFS). DELETE (classic
        # rollback journal) is NFS-safe. Override via config if on local disk.
        try:
            self._conn.execute(f"PRAGMA journal_mode={journal_mode}")
        except Exception:
            self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # never memory-map the DB file on NFS
        self._conn.execute("PRAGMA mmap_size=0")
        migrate(self._conn)

    # -- generic ------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(params))
            self._conn.commit()

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, [tuple(p) for p in seq])
            self._conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, row: dict[str, Any]) -> None:
        if not row:
            return
        cols = list(row.keys())
        placeholders = ",".join("?" for _ in cols)
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        self.execute(sql, [row[c] for c in cols])

    def insert_many(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        placeholders = ",".join("?" for _ in cols)
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        self.executemany(sql, [[r.get(c) for c in cols] for r in rows])

    def upsert(self, table: str, row: dict[str, Any], conflict_cols: list[str]) -> None:
        cols = list(row.keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in conflict_cols)
        sql = (
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({','.join(conflict_cols)}) DO UPDATE SET {updates}"
        )
        self.execute(sql, [row[c] for c in cols])

    # -- dataframe helper for the dashboard ---------------------------------
    def read_df(self, sql: str, params: Iterable[Any] = ()) -> pd.DataFrame:
        with self._lock:
            return pd.read_sql_query(sql, self._conn, params=tuple(params))

    def table_df(self, table: str, where: str = "", params: Iterable[Any] = (),
                 limit: Optional[int] = None) -> pd.DataFrame:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        try:
            return self.read_df(sql, params)
        except Exception:
            return pd.DataFrame()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
