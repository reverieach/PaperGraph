from __future__ import annotations

from pathlib import Path

from app.infrastructure.db import Database, run_migrations
from app.services.daily.daily_cache_store import get_cache, set_cache
from app.services.daily.daily_recommend_store import record_arxiv_recommendations
from app.services.feedback.negative_feedback_memory import record_skip_negative_pref
from app.services.graph.kg_relations import upsert_relations
from app.services.reader.paper_reader_context import _cache_set, extract_pdf_text_full_cached
from app.services.reader.reader_opening_cache import get_cached_opening, set_cached_opening


def _scoped_fixture_db(tmp_path) -> tuple[str, int, int, int, int, int]:
    db_path = str(tmp_path / "runtime-schema.db")
    run_migrations(db_path)
    with Database(db_path).transaction() as conn:
        owner_id = int(
            conn.execute(
                """
                INSERT INTO auth_users(username,password_hash,status,created_at,updated_at)
                VALUES('runtime-owner','hash','active',1,1)
                """
            ).lastrowid
        )
        other_id = int(
            conn.execute(
                """
                INSERT INTO auth_users(username,password_hash,status,created_at,updated_at)
                VALUES('runtime-other','hash','active',1,1)
                """
            ).lastrowid
        )
        source_id = int(
            conn.execute(
                "INSERT INTO papers(user_id,title,created_at,updated_at) VALUES(?,?,1,1)",
                (owner_id, "Owner source"),
            ).lastrowid
        )
        target_id = int(
            conn.execute(
                "INSERT INTO papers(user_id,title,created_at,updated_at) VALUES(?,?,1,1)",
                (owner_id, "Owner target"),
            ).lastrowid
        )
        other_paper_id = int(
            conn.execute(
                "INSERT INTO papers(user_id,title,created_at,updated_at) VALUES(?,?,1,1)",
                (other_id, "Other target"),
            ).lastrowid
        )
    return db_path, owner_id, other_id, source_id, target_id, other_paper_id


def test_negative_feedback_is_user_scoped_ttl_only_and_has_no_auto_longterm(tmp_path) -> None:
    db_path, owner_id, other_id, *_ = _scoped_fixture_db(tmp_path)
    assert record_skip_negative_pref(
        db_path,
        user_id=owner_id,
        identity_key="arxiv:owner-paper",
        title="Owner skipped paper",
        source="arxiv",
        keywords=["RAG"],
    )
    assert record_skip_negative_pref(
        db_path,
        user_id=other_id,
        identity_key="arxiv:other-paper",
        title="Other skipped paper",
        source="arxiv",
        keywords=["Agents"],
    )

    with Database(db_path).read() as conn:
        owner_rows = conn.execute(
            "SELECT identity_key,payload_json FROM negative_pref_memory WHERE user_id=?",
            (owner_id,),
        ).fetchall()
        other_rows = conn.execute(
            "SELECT identity_key FROM negative_pref_memory WHERE user_id=?",
            (other_id,),
        ).fetchall()
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert [str(row["identity_key"]) for row in owner_rows] == ["arxiv:owner-paper"]
    assert [str(row["identity_key"]) for row in other_rows] == ["arxiv:other-paper"]
    assert "negative_pref_longterm" not in tables


def test_daily_cache_and_recommendation_audit_are_user_scoped(tmp_path) -> None:
    db_path, owner_id, other_id, *_ = _scoped_fixture_db(tmp_path)
    set_cache(
        db_path,
        user_id=owner_id,
        date_key="2026-07-28",
        cache_key="daily-v1",
        payload={"owner": True},
    )
    assert get_cache(
        db_path,
        user_id=owner_id,
        date_key="2026-07-28",
        cache_key="daily-v1",
    ) == {"owner": True}
    assert get_cache(
        db_path,
        user_id=other_id,
        date_key="2026-07-28",
        cache_key="daily-v1",
    ) is None

    assert record_arxiv_recommendations(
        db_path,
        user_id=owner_id,
        date_key="2026-07-28",
        items=[("2401.00001", "Owner recommendation")],
    ) == 1
    with Database(db_path).read() as conn:
        owner_count = conn.execute(
            "SELECT COUNT(*) FROM daily_recommendations WHERE user_id=?",
            (owner_id,),
        ).fetchone()[0]
        other_count = conn.execute(
            "SELECT COUNT(*) FROM daily_recommendations WHERE user_id=?",
            (other_id,),
        ).fetchone()[0]
    assert owner_count == 1
    assert other_count == 0


def test_reader_caches_and_kg_relations_reject_cross_user_paper_scope(tmp_path) -> None:
    db_path, owner_id, other_id, source_id, target_id, other_paper_id = _scoped_fixture_db(tmp_path)
    set_cached_opening(
        db_path,
        source_id,
        "Owner-only opening",
        user_id=owner_id,
    )
    assert get_cached_opening(
        db_path,
        source_id,
        user_id=owner_id,
    )[0] == "Owner-only opening"
    assert get_cached_opening(
        db_path,
        source_id,
        user_id=other_id,
    ) == (None, False)

    pdf_path = Path(tmp_path) / "cache-fixture.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\ncache fixture\n")
    _cache_set(
        db_path,
        source_id,
        str(pdf_path),
        "Owner-only PDF cache",
        user_id=owner_id,
    )
    assert extract_pdf_text_full_cached(
        db_path,
        source_id,
        str(pdf_path),
        user_id=owner_id,
    ) == ("Owner-only PDF cache", True)
    assert extract_pdf_text_full_cached(
        db_path,
        source_id,
        str(pdf_path),
        user_id=other_id,
    ) == ("", False)

    inserted = upsert_relations(
        db_path,
        source_id,
        [
            {"target_paper_id": target_id, "relation": "extends", "score": 0.8},
            {"target_paper_id": other_paper_id, "relation": "extends", "score": 0.9},
        ],
        user_id=owner_id,
    )
    assert inserted == 1
    with Database(db_path).read() as conn:
        relation_rows = conn.execute(
            "SELECT user_id,target_paper_id FROM paper_relations"
        ).fetchall()
        opening_rows = conn.execute("SELECT user_id,paper_id FROM paper_opening_cache").fetchall()
        excerpt_rows = conn.execute("SELECT user_id,paper_id FROM paper_pdf_excerpt_cache").fetchall()
    assert [(int(row["user_id"]), int(row["target_paper_id"])) for row in relation_rows] == [
        (owner_id, target_id)
    ]
    assert [(int(row["user_id"]), int(row["paper_id"])) for row in opening_rows] == [
        (owner_id, source_id)
    ]
    assert [(int(row["user_id"]), int(row["paper_id"])) for row in excerpt_rows] == [
        (owner_id, source_id)
    ]
