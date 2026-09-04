from __future__ import annotations

from app.infrastructure.db import Database, run_migrations
from app.repositories.memory_repository import MemoryRepository
from app.services.memory.retriever import MemoryRetriever, format_memory_context


def _seed_memory_db(tmp_path) -> tuple[str, int, int, int]:
    db_path = str(tmp_path / "memory-retrieval.db")
    run_migrations(db_path)
    with Database(db_path).transaction() as conn:
        conn.execute(
            "INSERT INTO auth_users(id,username,password_hash,status,created_at,updated_at) VALUES(1,'alice','x','active',1,1)"
        )
        conn.execute(
            "INSERT INTO auth_users(id,username,password_hash,status,created_at,updated_at) VALUES(2,'bob','x','active',1,1)"
        )
        conn.execute(
            "INSERT INTO papers(id,user_id,title,created_at,updated_at) VALUES(10,1,'Current Paper',1,1)"
        )
        conn.execute(
            "INSERT INTO papers(id,user_id,title,created_at,updated_at) VALUES(11,1,'Other Paper',1,1)"
        )
        conn.execute(
            "INSERT INTO papers(id,user_id,title,created_at,updated_at) VALUES(20,2,'Bob Paper',1,1)"
        )

        def add(
            memory_id: str,
            *,
            scope_type: str,
            scope_id: str,
            content: str,
            confirmed: int = 1,
            status: str = "active",
            expires_at: int | None = None,
        ) -> None:
            conn.execute(
                """
                INSERT INTO memories(
                    id,user_id,scope_type,scope_id,kind,content,content_hash,
                    source_type,confirmed_by_user,status,metadata_json,
                    created_at,updated_at,importance,expires_at,superseded_by
                ) VALUES(?,?,?,?,?,?,?,'test',?,?, '{}',1,1,0.5,?,NULL)
                """,
                (
                    memory_id,
                    1,
                    scope_type,
                    scope_id,
                    "key_finding",
                    content,
                    f"hash-{memory_id}",
                    confirmed,
                    status,
                    expires_at,
                ),
            )

        add(
            "paper-current",
            scope_type="paper",
            scope_id="10",
            content="本文的检索增强生成方法使用混合召回和重排序。",
        )
        add(
            "paper-other",
            scope_type="paper",
            scope_id="11",
            content="另一篇论文也讨论检索增强生成，但它不属于当前论文。",
        )
        add(
            "user-relevant",
            scope_type="user",
            scope_id="1",
            content="我的研究目标是比较混合召回与重排序。",
        )
        add(
            "user-irrelevant",
            scope_type="user",
            scope_id="1",
            content="我喜欢蓝色界面。",
        )
        add(
            "expired",
            scope_type="user",
            scope_id="1",
            content="检索增强生成的过期偏好。",
            expires_at=1,
        )
        add(
            "unconfirmed",
            scope_type="paper",
            scope_id="10",
            content="检索增强生成的未确认内容。",
            confirmed=0,
        )
        add(
            "superseded",
            scope_type="paper",
            scope_id="10",
            content="检索增强生成的过时结论。",
            status="superseded",
        )
    return db_path, 1, 10, 11


def test_memory_retriever_is_scope_safe_relevance_gated_and_non_citable(tmp_path) -> None:
    db_path, user_id, paper_id, other_paper_id = _seed_memory_db(tmp_path)
    repository = MemoryRepository(db_path)
    result = MemoryRetriever(repository).retrieve(
        user_id=user_id,
        paper_id=paper_id,
        query="请解释这篇论文的检索增强生成混合召回方法",
    )

    ids = {hit.memory_id for hit in result.hits}
    assert "paper-current" in ids
    assert "user-relevant" in ids
    assert ids.isdisjoint(
        {"paper-other", "user-irrelevant", "expired", "unconfirmed", "superseded"}
    )
    assert all(not hit.citation_allowed for hit in result.hits)
    assert all(hit.inclusion_reason for hit in result.hits)
    assert "【用户确认的相关记忆】" in format_memory_context(result.hits)
    if repository.has_memory_trigram_fts():
        # This row was inserted after migration, so this also verifies the
        # external-content FTS trigger rather than only the fallback scan.
        assert any(
            "trigram_fts" in hit.inclusion_reason
            for hit in result.hits
            if hit.memory_id == "paper-current"
        )

    # A paper ID owned by another user cannot be used to retrieve Alice's
    # global memory or a cross-paper Memory row.
    cross_user_scope = MemoryRetriever(repository).retrieve(
        user_id=user_id,
        paper_id=20,
        query="检索增强生成",
    )
    assert not cross_user_scope.hits
    other_paper = MemoryRetriever(repository).retrieve(
        user_id=user_id,
        paper_id=other_paper_id,
        query="检索增强生成",
    )
    assert all(hit.scope_id != str(paper_id) for hit in other_paper.hits)


def test_memory_retriever_opening_is_paper_only_and_soft_delete_removes_hits(tmp_path) -> None:
    db_path, user_id, paper_id, _ = _seed_memory_db(tmp_path)
    repository = MemoryRepository(db_path)
    opening = MemoryRetriever(repository).retrieve(
        user_id=user_id,
        paper_id=paper_id,
        query="",
    )
    assert opening.hits
    assert {hit.scope_type for hit in opening.hits} == {"paper"}

    assert repository.delete_memory(user_id=user_id, memory_id="paper-current")
    after_delete = MemoryRetriever(repository).retrieve(
        user_id=user_id,
        paper_id=paper_id,
        query="检索增强生成混合召回",
    )
    assert "paper-current" not in {hit.memory_id for hit in after_delete.hits}


def test_memory_retrieval_policy_and_supersede_are_effective(tmp_path) -> None:
    db_path, user_id, paper_id, _ = _seed_memory_db(tmp_path)
    repository = MemoryRepository(db_path)
    with Database(db_path).transaction() as conn:
        conn.execute(
            """
            INSERT INTO memories(
                id,user_id,scope_type,scope_id,kind,content,content_hash,
                source_type,confirmed_by_user,status,metadata_json,
                created_at,updated_at,importance,expires_at,superseded_by
            ) VALUES('paper-replacement',1,'paper','10','key_finding',
                     '本文的混合召回使用重排序作为第二阶段。',
                     'hash-paper-replacement','test',1,'active','{}',1,1,
                     0.8,NULL,NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO memories(
                id,user_id,scope_type,scope_id,kind,content,content_hash,
                source_type,confirmed_by_user,status,metadata_json,
                created_at,updated_at,importance,expires_at,superseded_by
            ) VALUES('paper-expiring',1,'paper','10','key_finding',
                     '本文的检索增强生成过期测试结论。',
                     'hash-paper-expiring','test',1,'active','{}',1,1,
                     0.5,NULL,NULL)
            """
        )

    updated = repository.update_memory_retrieval_policy(
        user_id=user_id,
        memory_id="paper-expiring",
        importance=0.9,
        expires_at=1,
    )
    assert updated is not None
    assert updated["importance"] == 0.9
    assert updated["expires_at"] == 1

    before_supersede = MemoryRetriever(repository).retrieve(
        user_id=user_id,
        paper_id=paper_id,
        query="检索增强生成过期测试",
    )
    assert "paper-expiring" not in {hit.memory_id for hit in before_supersede.hits}
    assert not repository.supersede_memory(
        user_id=user_id,
        memory_id="paper-current",
        replacement_memory_id="paper-expiring",
    )
    assert repository.supersede_memory(
        user_id=user_id,
        memory_id="paper-current",
        replacement_memory_id="paper-replacement",
    )
    # A superseded row is excluded even when its content would lexically match.
    after_supersede = MemoryRetriever(repository).retrieve(
        user_id=user_id,
        paper_id=paper_id,
        query="混合召回重排序",
    )
    ids = {hit.memory_id for hit in after_supersede.hits}
    assert "paper-current" not in ids
    assert "paper-replacement" in ids


def test_memory_retriever_keeps_direct_user_memory_match_when_fts_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    db_path, user_id, paper_id, _ = _seed_memory_db(tmp_path)
    repository = MemoryRepository(db_path)
    stored, inserted = repository.add_user_memory(
        user_id=user_id,
        kind="preference",
        content="我偏好使用检索词 unambiguous-token。",
    )
    assert inserted
    monkeypatch.setattr(repository, "has_memory_fts", lambda: False)
    monkeypatch.setattr(repository, "has_memory_trigram_fts", lambda: False)

    result = MemoryRetriever(repository).retrieve(
        user_id=user_id,
        paper_id=paper_id,
        query="请问 unambiguous-token 如何使用？",
    )

    assert stored["id"] in {hit.memory_id for hit in result.hits}
    assert result.degraded
    assert "memory_unicode61_fts_unavailable" in result.degradation_reasons
