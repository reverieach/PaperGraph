from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, cast


class Database:
    """Small SQLite connection factory with one set of safety pragmas."""

    def __init__(
        self,
        db_path: str,
        *,
        busy_timeout_ms: int = 5000,
        read_only: bool = False,
    ) -> None:
        self.db_path = str(db_path)
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self.read_only = bool(read_only)

    def _ensure_parent(self) -> None:
        if self.db_path == ":memory:" or self.db_path.startswith("file:"):
            return
        Path(os.path.abspath(self.db_path)).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        timeout = max(1.0, self.busy_timeout_ms / 1000)
        if self.read_only:
            if self.db_path == ":memory:" or self.db_path.startswith("file:"):
                raise ValueError("read-only Database requires a filesystem SQLite path")
            database_uri = f"{Path(self.db_path).expanduser().resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(database_uri, uri=True, timeout=timeout)
        else:
            self._ensure_parent()
            conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        if not self.read_only and self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query_one(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> sqlite3.Row | None:
        with self.read() as conn:
            return cast(sqlite3.Row | None, conn.execute(sql, params).fetchone())

    def query_all(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> list[sqlite3.Row]:
        with self.read() as conn:
            return list(conn.execute(sql, params).fetchall())
