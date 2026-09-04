from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.infrastructure.db import Database, run_migrations
from app.repositories.memory_repository import (
    MemoryConflictError,
    MemoryOwnershipError,
    MemoryRepository,
)
from app.services.memory.memory_draft_service import (
    MemoryDraftError,
    MemoryDraftService,
)
from app.services.reader.paper_reader_history import (
    append_exchange,
    ensure_conversation,
    ensure_opening_turn,
    list_turns,
)
from app.services.reader.paper_reader_context import format_paper_reader_block
from app.services.reader.paper_reader_service import (
    _is_explicit_memory_write_request,
)
from app.core.paper import Paper


class _FakeLLM:
    model = "fake-memory-model"

    def chat(self, messages, **kwargs):
        del messages, kwargs
        return SimpleNamespace(
            content=json.dumps(
                {
                    "paper_summary": "本文提出了一个可验证的方法。",
                    "key_findings": [
                        {
                            "content": "实验结果优于基线。",
                            "evidence_turn_ids": [2, 3],
                        }
                    ],
                    "open_questions": [],
                    "research_decisions": [],
                    "user_memory_candidates": [
                        {
                            "kind": "research_goal",
                            "content": "后续比较混合检索。",
                            "confidence": 0.82,
                            "evidence_turn_ids": [2],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )


def _seed_scope(db_path: str) -> tuple[int, int, str]:
    run_migrations(db_path)
    with Database(db_path).transaction() as conn:
        user_cursor = conn.execute(
            """
            INSERT INTO auth_users(
                username,password_hash,status,created_at,updated_at
            ) VALUES('alice','x','active',1,1)
            """
        )
        assert user_cursor.lastrowid is not None
        user_id = int(user_cursor.lastrowid)
        paper_cursor = conn.execute(
            """
            INSERT INTO papers(
                user_id,title,created_at,updated_at
            ) VALUES(?,?,1,1)
            """,
            (user_id, "Memory Test Paper"),
        )
        assert paper_cursor.lastrowid is not None
        paper_id = int(paper_cursor.lastrowid)
    conversation_id = ensure_conversation(
        db_path,
        user_id=user_id,
        paper_id=paper_id,
    )
    ensure_opening_turn(
        db_path,
        user_id=user_id,
        paper_id=paper_id,
        conversation_id=conversation_id,
        opening_text="导读",
    )
    ensure_opening_turn(
        db_path,
        user_id=user_id,
        paper_id=paper_id,
        conversation_id=conversation_id,
        opening_text="导读",
    )
    append_exchange(
        db_path,
        user_id=user_id,
        paper_id=paper_id,
        conversation_id=conversation_id,
        user_message="核心贡献是什么？",
        assistant_reply="核心贡献是统一了检索流程。",
    )
    return user_id, paper_id, conversation_id


def test_memory_draft_commit_delete_restart_and_isolation(tmp_path) -> None:
    db_path = str(tmp_path / "memory.db")
    user_id, paper_id, conversation_id = _seed_scope(db_path)
    service = MemoryDraftService(db_path, llm=_FakeLLM())

    draft = service.generate_draft(
        user_id=user_id,
        paper_id=paper_id,
        conversation_id=conversation_id,
        from_turn_id=None,
        to_turn_id=None,
    )
    assert draft["status"] == "draft"
    assert draft["from_turn_id"] == 1
    assert draft["to_turn_id"] == 3

    repository = MemoryRepository(db_path)
    result = repository.commit_draft(
        user_id=user_id,
        draft_id=draft["id"],
        paper_items=[
            {
                "kind": "reading_summary",
                "content": draft["payload"]["paper_summary"],
            },
            {
                "kind": "key_finding",
                "content": "实验结果优于基线。",
            },
            {
                "kind": "key_finding",
                "content": "  实验结果优于基线。  ",
            },
        ],
        accepted_user_items=[
            {
                "kind": "research_goal",
                "content": "后续比较混合检索。",
            }
        ],
        idempotency_key="memory-commit-0001",
    )
    assert len(result["memories"]) == 3

    duplicate = repository.commit_draft(
        user_id=user_id,
        draft_id=draft["id"],
        paper_items=[],
        accepted_user_items=[],
        idempotency_key="memory-commit-0001",
    )
    assert duplicate == result
    with pytest.raises(MemoryConflictError):
        repository.commit_draft(
            user_id=user_id,
            draft_id=draft["id"],
            paper_items=[],
            accepted_user_items=[],
            idempotency_key="memory-commit-other",
        )

    restarted_repository = MemoryRepository(db_path)
    memories = restarted_repository.list_memories(
        user_id=user_id,
        limit=20,
    )
    assert len(memories) == 3
    assert restarted_repository.list_memories(user_id=user_id + 1) == []
    with pytest.raises(MemoryOwnershipError):
        restarted_repository.commit_draft(
            user_id=user_id + 1,
            draft_id=draft["id"],
            paper_items=[
                {"kind": "reading_summary", "content": "cross-user"}
            ],
            accepted_user_items=[],
            idempotency_key="memory-cross-user",
        )

    context = restarted_repository.build_paper_context(
        user_id=user_id,
        paper_id=paper_id,
        query="实验基线",
    )
    assert "实验结果优于基线" in context
    assert restarted_repository.delete_memory(
        user_id=user_id,
        memory_id=memories[0]["id"],
    )
    assert len(
        restarted_repository.list_memories(user_id=user_id, limit=20)
    ) == 2


def test_memory_draft_rejects_wrong_scope_and_fake_evidence(tmp_path) -> None:
    db_path = str(tmp_path / "memory-invalid.db")
    user_id, paper_id, conversation_id = _seed_scope(db_path)

    with pytest.raises(MemoryDraftError, match="不存在"):
        MemoryDraftService(db_path, llm=_FakeLLM()).generate_draft(
            user_id=user_id + 1,
            paper_id=paper_id,
            conversation_id=conversation_id,
            from_turn_id=None,
            to_turn_id=None,
        )

    class FakeEvidenceLLM(_FakeLLM):
        def chat(self, messages, **kwargs):
            result = super().chat(messages, **kwargs)
            payload = json.loads(result.content)
            payload["key_findings"][0]["evidence_turn_ids"] = [99999]
            return SimpleNamespace(content=json.dumps(payload))

    with pytest.raises(MemoryDraftError, match="evidence_turn_ids"):
        MemoryDraftService(db_path, llm=FakeEvidenceLLM()).generate_draft(
            user_id=user_id,
            paper_id=paper_id,
            conversation_id=conversation_id,
            from_turn_id=None,
            to_turn_id=None,
        )

    turns = list_turns(
        db_path,
        user_id=user_id,
        paper_id=paper_id,
        conversation_id=conversation_id,
    )
    assert [turn["role"] for turn in turns] == [
        "assistant",
        "user",
        "assistant",
    ]


def test_memory_draft_cancel_is_idempotent_and_isolated(tmp_path) -> None:
    db_path = str(tmp_path / "memory-cancel.db")
    user_id, paper_id, conversation_id = _seed_scope(db_path)
    draft = MemoryDraftService(db_path, llm=_FakeLLM()).generate_draft(
        user_id=user_id,
        paper_id=paper_id,
        conversation_id=conversation_id,
        from_turn_id=None,
        to_turn_id=None,
    )
    repository = MemoryRepository(db_path)

    cancelled = repository.cancel_draft(
        user_id=user_id,
        draft_id=draft["id"],
    )
    assert cancelled["status"] == "cancelled"
    repeated = repository.cancel_draft(
        user_id=user_id,
        draft_id=draft["id"],
    )
    assert repeated["status"] == "cancelled"
    with pytest.raises(MemoryOwnershipError):
        repository.cancel_draft(
            user_id=user_id + 1,
            draft_id=draft["id"],
        )
    with pytest.raises(MemoryConflictError):
        repository.commit_draft(
            user_id=user_id,
            draft_id=draft["id"],
            paper_items=[
                {"kind": "reading_summary", "content": "不应写入"}
            ],
            accepted_user_items=[],
            idempotency_key="cancelled-draft-commit",
        )
    assert repository.list_memories(user_id=user_id) == []


def test_reader_memory_write_intent_requires_manual_confirmation() -> None:
    assert _is_explicit_memory_write_request("请记住，我对 RAG 感兴趣")
    assert _is_explicit_memory_write_request("把这个偏好加入长期记忆")
    assert _is_explicit_memory_write_request("记下来：下次比较混合检索")
    assert not _is_explicit_memory_write_request("你还记得这篇论文的方法吗？")
    assert not _is_explicit_memory_write_request("请解释 RAG 的召回流程")


def test_web_reader_context_is_explicitly_not_pdf_evidence() -> None:
    block = format_paper_reader_block(
        Paper(title="Web Context Paper"),
        "",
        web_context="A search-engine snippet.",
    )
    assert "source_type=web" in block
    assert "不能作为 PDF 页码引用" in block
    assert "【PDF 正文" not in block
