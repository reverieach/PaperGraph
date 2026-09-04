from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ...infrastructure.db import Database


def default_conversation_id(user_id: int, paper_id: int) -> str:
    return f"paper-{int(user_id)}-{int(paper_id)}"


def ensure_conversation(
    db_path: str,
    *,
    user_id: int,
    paper_id: int,
    conversation_id: str | None = None,
    title: str | None = None,
) -> str:
    conversation_id = (
        str(conversation_id or "").strip()
        or default_conversation_id(user_id, paper_id)
    )
    now = int(time.time())
    with Database(db_path).transaction() as conn:
        paper = conn.execute(
            "SELECT 1 FROM papers WHERE id=? AND user_id=?",
            (int(paper_id), int(user_id)),
        ).fetchone()
        if not paper:
            raise LookupError("paper not found")
        existing = conn.execute(
            """
            SELECT id FROM reader_conversations
            WHERE id=? AND user_id=? AND paper_id=?
            """,
            (conversation_id, int(user_id), int(paper_id)),
        ).fetchone()
        if existing:
            return conversation_id
        id_collision = conn.execute(
            "SELECT 1 FROM reader_conversations WHERE id=?",
            (conversation_id,),
        ).fetchone()
        if id_collision:
            conversation_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO reader_conversations(
                id,user_id,paper_id,title,created_at,updated_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                conversation_id,
                int(user_id),
                int(paper_id),
                str(title or "").strip() or None,
                now,
                now,
            ),
        )
    return conversation_id


def append_turn(
    db_path: str,
    *,
    user_id: int,
    paper_id: int,
    conversation_id: str | None,
    role: str,
    content: str,
    metadata: dict[str, Any] | str | None = None,
) -> int | None:
    text = str(content or "").strip()
    if not text:
        return None
    role = str(role or "").strip().lower()
    if role not in {"user", "assistant", "tool"}:
        raise ValueError(f"unsupported reader role: {role}")
    conversation_id = ensure_conversation(
        db_path,
        user_id=int(user_id),
        paper_id=int(paper_id),
        conversation_id=conversation_id,
    )
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            metadata_obj = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            metadata_obj = {}
    else:
        metadata_obj = metadata or {}
    now = int(time.time())
    with Database(db_path).transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO paper_reader_turns(
                user_id,paper_id,conversation_id,role,content,
                metadata_json,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                int(user_id),
                int(paper_id),
                conversation_id,
                role,
                text,
                json.dumps(metadata_obj, ensure_ascii=False),
                now,
            ),
        )
        conn.execute(
            "UPDATE reader_conversations SET updated_at=? "
            "WHERE id=? AND user_id=? AND paper_id=?",
            (now, conversation_id, int(user_id), int(paper_id)),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("reader turn insert did not return an id")
        return int(cursor.lastrowid)


def append_exchange(
    db_path: str,
    *,
    user_id: int,
    paper_id: int,
    conversation_id: str | None,
    user_message: str,
    assistant_reply: str,
    assistant_metadata: dict[str, Any] | str | None = None,
) -> tuple[int, int]:
    user_text = str(user_message or "").strip()
    assistant_text = str(assistant_reply or "").strip()
    if not user_text or not assistant_text:
        raise ValueError("reader exchange requires non-empty user and assistant text")
    conversation_id = ensure_conversation(
        db_path,
        user_id=int(user_id),
        paper_id=int(paper_id),
        conversation_id=conversation_id,
    )
    if isinstance(assistant_metadata, str):
        try:
            parsed = json.loads(assistant_metadata)
            metadata_obj = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            metadata_obj = {}
    else:
        metadata_obj = assistant_metadata or {}
    now = int(time.time())
    with Database(db_path).transaction() as conn:
        user_cursor = conn.execute(
            """
            INSERT INTO paper_reader_turns(
                user_id,paper_id,conversation_id,role,content,
                metadata_json,created_at
            ) VALUES(?,?,?,'user',?,'{}',?)
            """,
            (
                int(user_id),
                int(paper_id),
                conversation_id,
                user_text,
                now,
            ),
        )
        assistant_cursor = conn.execute(
            """
            INSERT INTO paper_reader_turns(
                user_id,paper_id,conversation_id,role,content,
                metadata_json,created_at
            ) VALUES(?,?,?,'assistant',?,?,?)
            """,
            (
                int(user_id),
                int(paper_id),
                conversation_id,
                assistant_text,
                json.dumps(metadata_obj, ensure_ascii=False),
                now,
            ),
        )
        conn.execute(
            "UPDATE reader_conversations SET updated_at=? "
            "WHERE id=? AND user_id=? AND paper_id=?",
            (now, conversation_id, int(user_id), int(paper_id)),
        )
        if user_cursor.lastrowid is None or assistant_cursor.lastrowid is None:
            raise RuntimeError("reader exchange insert did not return row ids")
        return int(user_cursor.lastrowid), int(assistant_cursor.lastrowid)


def ensure_opening_turn(
    db_path: str,
    *,
    user_id: int,
    paper_id: int,
    conversation_id: str | None,
    opening_text: str,
) -> None:
    opening = str(opening_text or "").strip()
    if not opening:
        return
    conversation_id = ensure_conversation(
        db_path,
        user_id=int(user_id),
        paper_id=int(paper_id),
        conversation_id=conversation_id,
    )
    with Database(db_path).transaction() as conn:
        first = conn.execute(
            """
            SELECT id,role,content FROM paper_reader_turns
            WHERE user_id=? AND paper_id=? AND conversation_id=?
            ORDER BY id ASC LIMIT 1
            """,
            (int(user_id), int(paper_id), conversation_id),
        ).fetchone()
        if first is None:
            conn.execute(
                """
                INSERT INTO paper_reader_turns(
                    user_id,paper_id,conversation_id,role,content,
                    metadata_json,created_at
                ) VALUES(?,?,?,'assistant',?,'{}',?)
                """,
                (
                    int(user_id),
                    int(paper_id),
                    conversation_id,
                    opening,
                    int(time.time()),
                ),
            )
            return
        if str(first["role"]) == "assistant":
            if str(first["content"]) != opening:
                conn.execute(
                    """
                    UPDATE paper_reader_turns SET content=?
                    WHERE id=? AND user_id=? AND paper_id=? AND conversation_id=?
                    """,
                    (
                        opening,
                        int(first["id"]),
                        int(user_id),
                        int(paper_id),
                        conversation_id,
                    ),
                )
            return
        conn.execute(
            """
            INSERT INTO paper_reader_turns(
                user_id,paper_id,conversation_id,role,content,
                metadata_json,created_at
            ) VALUES(?,?,?,'assistant',?,'{}',?)
            """,
            (
                int(user_id),
                int(paper_id),
                conversation_id,
                opening,
                max(0, int(time.time()) - 1),
            ),
        )


def list_turns(
    db_path: str,
    *,
    user_id: int,
    paper_id: int,
    conversation_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conversation_id = (
        str(conversation_id or "").strip()
        or default_conversation_id(user_id, paper_id)
    )
    with Database(db_path).read() as conn:
        rows = conn.execute(
            """
            SELECT id,role,content,metadata_json,created_at
            FROM paper_reader_turns
            WHERE user_id=? AND paper_id=? AND conversation_id=?
            ORDER BY id ASC
            LIMIT ?
            """,
            (
                int(user_id),
                int(paper_id),
                conversation_id,
                max(1, int(limit)),
            ),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "role": str(row["role"]),
            "content": str(row["content"]),
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "created_at": int(row["created_at"]),
        }
        for row in rows
    ]
