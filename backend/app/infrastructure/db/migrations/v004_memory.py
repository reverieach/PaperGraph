from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
import uuid

from .helpers import (
    columns,
    legacy_owner_id,
    now_ts,
    quote_identifier,
    rename_to_backup,
    table_exists,
)


MEMORIES_SQL = """
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    scope_type TEXT NOT NULL CHECK(scope_type IN ('paper', 'conversation', 'user')),
    scope_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT,
    source_turn_from INTEGER,
    source_turn_to INTEGER,
    confirmed_by_user INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'superseded', 'deleted')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
"""


def _normalize_content(content: str) -> str:
    text = unicodedata.normalize("NFKC", str(content or "")).strip()
    return " ".join(text.split())


def _content_hash(content: str) -> str:
    return hashlib.sha256(_normalize_content(content).encode("utf-8")).hexdigest()


def _create_drafts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_drafts (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES auth_users(id),
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            conversation_id TEXT NOT NULL REFERENCES reader_conversations(id) ON DELETE CASCADE,
            from_turn_id INTEGER NOT NULL,
            to_turn_id INTEGER NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('draft', 'committed', 'cancelled', 'expired')),
            payload_json TEXT NOT NULL,
            source_snapshot_hash TEXT NOT NULL,
            llm_model TEXT,
            commit_idempotency_key TEXT,
            commit_result_json TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            committed_at INTEGER
        )
        """
    )


def _is_canonical(conn: sqlite3.Connection) -> bool:
    required = {
        "id",
        "user_id",
        "scope_type",
        "scope_id",
        "kind",
        "content",
        "content_hash",
        "source_type",
        "confirmed_by_user",
        "status",
        "metadata_json",
        "created_at",
        "updated_at",
    }
    return required.issubset(columns(conn, "memories"))


def _json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _insert_legacy_memory(
    conn: sqlite3.Connection,
    *,
    owner_id: int,
    row: sqlite3.Row,
    source_table: str,
) -> None:
    keys = set(row.keys())
    content = _normalize_content(str(row["content"] or "")) if "content" in keys else ""
    if not content:
        return
    props = _json_object(
        row["properties"]
        if "properties" in keys
        else row["metadata"]
        if "metadata" in keys
        else row["meta_json"]
        if "meta_json" in keys
        else None
    )
    raw_uid = str(row["user_id"] or "") if "user_id" in keys else ""
    paper_id = props.get("paper_id")
    if paper_id is None and raw_uid.startswith("papergraph:paper:"):
        paper_id = raw_uid.rsplit(":", 1)[-1]
    if paper_id is None and "paper_id" in keys:
        paper_id = row["paper_id"]
    scope_type = "paper" if paper_id not in (None, "", 0, "0") else "user"
    scope_id = str(paper_id) if scope_type == "paper" else str(owner_id)
    kind = str(
        row["memory_type"]
        if "memory_type" in keys
        else row["kind"]
        if "kind" in keys
        else "legacy"
    )
    raw_id = (
        row["id"]
        if "id" in keys and row["id"] not in (None, "")
        else row["memory_id"]
        if "memory_id" in keys and row["memory_id"] not in (None, "")
        else uuid.uuid4().hex
    )
    memory_id = f"legacy-{source_table}-{raw_id}"
    created = now_ts()
    for candidate in ("created_at", "timestamp", "updated_at"):
        if candidate in keys and row[candidate] not in (None, ""):
            try:
                created = int(float(row[candidate]))
                break
            except (TypeError, ValueError):
                pass
    metadata = dict(props)
    metadata["legacy_source_table"] = source_table
    conn.execute(
        """
        INSERT OR IGNORE INTO memories(
            id,user_id,scope_type,scope_id,kind,content,content_hash,
            source_type,source_id,confirmed_by_user,status,metadata_json,
            created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,0,'active',?,?,?)
        """,
        (
            memory_id,
            owner_id,
            scope_type,
            scope_id,
            kind,
            content,
            _content_hash(content),
            "legacy",
            str(raw_id),
            json.dumps(metadata, ensure_ascii=False),
            created,
            created,
        ),
    )


def migrate(conn: sqlite3.Connection) -> None:
    legacy_tables: list[str] = []
    if table_exists(conn, "memories") and not _is_canonical(conn):
        backup = rename_to_backup(conn, "memories")
        if backup:
            legacy_tables.append(backup)
    if not table_exists(conn, "memories"):
        conn.execute(MEMORIES_SQL)

    if table_exists(conn, "agent_memory"):
        backup = rename_to_backup(conn, "agent_memory")
        if backup:
            legacy_tables.append(backup)

    _create_drafts(conn)
    if legacy_tables:
        owner_id = legacy_owner_id(conn)
        for table in legacy_tables:
            for row in conn.execute(
                f"SELECT * FROM {quote_identifier(table)}"
            ).fetchall():
                _insert_legacy_memory(
                    conn,
                    owner_id=owner_id,
                    row=row,
                    source_table=table,
                )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_drafts_scope
            ON memory_drafts(user_id, paper_id, conversation_id, status, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_drafts_commit_key
            ON memory_drafts(user_id, commit_idempotency_key)
            WHERE commit_idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_scope
            ON memories(user_id, scope_type, scope_id, kind, status, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_active_content
            ON memories(user_id, scope_type, scope_id, kind, content_hash)
            WHERE status = 'active'
        """
    )
