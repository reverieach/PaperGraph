from __future__ import annotations

import pytest

from app.core.paper import Paper
from app.core.storage import PaperDatabase
from app.services.auth import user_service


def test_auth_tokens_and_paper_ownership_are_isolated(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = str(tmp_path / "data" / "auth.db")
    monkeypatch.setenv(
        "PAPERGRAPH_JWT_SECRET",
        "phase-1-test-secret-with-at-least-32-characters",
    )
    monkeypatch.setattr(user_service, "_get_db_path", lambda: db_path)

    alice = user_service.register_user("alice", "secret1")
    bob = user_service.register_user("bob", "secret2")
    assert alice["success"] and bob["success"]
    assert alice["user_id"] != bob["user_id"]
    assert not user_service.register_user("alice", "secret3")["success"]
    assert not user_service.login_user("alice", "wrong")["success"]

    login = user_service.login_user("alice", "secret1")
    assert login["success"]
    verified = user_service.get_user_from_token(str(login["token"]))
    assert verified == {
        "user_id": alice["user_id"],
        "username": "alice",
    }
    assert user_service.get_user_from_token(f"{login['token']}bad") is None

    database = PaperDatabase(db_path)
    paper_id, created = database.add_paper(
        Paper(title="Alice Paper", doi="10.1000/isolation"),
        user_id=int(alice["user_id"]),
    )
    assert created
    assert paper_id > 0
    assert database.get_paper_by_id(
        paper_id,
        user_id=int(alice["user_id"]),
    ) is not None
    assert database.get_paper_by_id(
        paper_id,
        user_id=int(bob["user_id"]),
    ) is None
    assert database.count_papers(user_id=int(alice["user_id"])) == 1
    assert database.count_papers(user_id=int(bob["user_id"])) == 0
    bob_paper_id, bob_created = database.add_paper(
        Paper(title="Bob Copy", doi="10.1000/isolation"),
        user_id=int(bob["user_id"]),
    )
    assert bob_created
    assert bob_paper_id != paper_id
    assert database.count_papers(user_id=int(bob["user_id"])) == 1
    assert not database.update_paper(
        paper_id,
        user_id=int(bob["user_id"]),
        notes="must not leak",
    )
    assert not database.delete_paper(
        paper_id,
        user_id=int(bob["user_id"]),
    )
    outside_pdf = tmp_path / "outside.pdf"
    outside_pdf.write_bytes(b"%PDF-1.4 test")
    assert database.set_local_pdf_path(
        paper_id,
        "../outside.pdf",
        user_id=int(alice["user_id"]),
    )
    with pytest.raises(ValueError, match="ordering"):
        database.get_all_papers(
            user_id=int(alice["user_id"]),
            order_by="created_at DESC; DROP TABLE papers",
        )
    assert database.delete_paper(
        paper_id,
        user_id=int(alice["user_id"]),
    )
    assert outside_pdf.exists()
