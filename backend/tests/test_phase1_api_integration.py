from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.dependencies import reset_dependencies
from app.api.main import app, settings
from app.core.author import Author
from app.core.paper import Paper
from app.core.storage import PaperDatabase
from app.services.memory.memory_draft_service import MemoryDraftService
from app.services.reader.paper_reader_history import (
    append_exchange,
    ensure_conversation,
    ensure_opening_turn,
)


class _ApiMemoryLLM:
    model = "api-memory-fake"

    def chat(self, messages, **kwargs):
        del messages, kwargs
        return SimpleNamespace(
            content=json.dumps(
                {
                    "paper_summary": "API 集成测试总结",
                    "key_findings": [
                        {
                            "content": "确认写入由用户触发。",
                            "evidence_turn_ids": [2, 3],
                        }
                    ],
                    "open_questions": [],
                    "research_decisions": [],
                    "user_memory_candidates": [],
                },
                ensure_ascii=False,
            )
        )


def test_authenticated_api_and_memory_confirmation_flow(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "PAPERGRAPH_JWT_SECRET",
        "phase-1-api-secret-with-at-least-32-characters",
    )
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(
        MemoryDraftService,
        "_get_llm",
        lambda self: _ApiMemoryLLM(),
    )
    reset_dependencies()

    try:
        with TestClient(app) as client:
            missing_auth = client.get("/api/papers/library")
            assert missing_auth.status_code == 401
            assert missing_auth.json()["error_code"] == "AUTH_REQUIRED"
            assert missing_auth.json()["request_id"]
            assert missing_auth.headers["X-Request-ID"] == missing_auth.json()["request_id"]
            invalid_request = client.post(
                "/api/auth/register",
                json={"username": "x", "password": "short"},
            )
            assert invalid_request.status_code == 422
            assert invalid_request.json()["error_code"] == "INVALID_REQUEST"

            registered = client.post(
                "/api/auth/register",
                json={"username": "api-alice", "password": "secret1"},
            )
            assert registered.status_code == 200
            auth_payload = registered.json()
            assert auth_payload["success"] is True
            user_id = int(auth_payload["user_id"])
            headers = {"Authorization": f"Bearer {auth_payload['token']}"}

            verified = client.get("/api/auth/verify", headers=headers)
            assert verified.status_code == 200
            assert verified.json()["user_id"] == user_id

            database = PaperDatabase(str(tmp_path / "papers.db"))
            paper_id, created = database.add_paper(
                Paper(
                    title="API Paper",
                    doi="10.1000/api",
                    authors=[
                        Author(name="First Author"),
                        Author(name="Second Author"),
                    ],
                ),
                user_id=user_id,
            )
            assert created
            relative_pdf = "文献库/api/api-paper.pdf"
            local_pdf = Path(tmp_path) / relative_pdf
            local_pdf.parent.mkdir(parents=True, exist_ok=True)
            local_pdf.write_bytes(b"%PDF-1.4\napi-ingest-fixture\n")
            assert database.set_local_pdf_path(
                paper_id,
                relative_pdf,
                user_id=user_id,
            )
            enqueue_response = client.post(
                f"/api/papers/{paper_id}/ingest",
                headers=headers,
            )
            assert enqueue_response.status_code == 200
            queued_job = enqueue_response.json()
            assert queued_job["status"] == "queued"
            ingest_status = client.get(
                f"/api/papers/{paper_id}/ingest",
                headers=headers,
            )
            assert ingest_status.status_code == 200
            assert ingest_status.json()["latest_job"]["id"] == queued_job["job_id"]
            assert ingest_status.json()["rag_ready"] is False
            capabilities = client.get("/health/capabilities")
            assert capabilities.status_code == 200
            assert "sqlite" in capabilities.json()["capabilities"]
            conversation_id = ensure_conversation(
                database.db_path,
                user_id=user_id,
                paper_id=paper_id,
            )
            ensure_opening_turn(
                database.db_path,
                user_id=user_id,
                paper_id=paper_id,
                conversation_id=conversation_id,
                opening_text="API 导读",
            )
            append_exchange(
                database.db_path,
                user_id=user_id,
                paper_id=paper_id,
                conversation_id=conversation_id,
                user_message="如何写入记忆？",
                assistant_reply="先生成草稿，再由用户确认。",
            )

            paper_response = client.get(
                f"/api/papers/{paper_id}",
                headers=headers,
            )
            assert paper_response.status_code == 200
            assert paper_response.json()["title"] == "API Paper"

            history = client.get(
                "/api/ai/paper-reader/history",
                params={
                    "paper_id": paper_id,
                    "conversation_id": conversation_id,
                },
                headers=headers,
            )
            assert history.status_code == 200
            assert [item["id"] for item in history.json()["turns"]] == [1, 2, 3]

            draft_response = client.post(
                f"/api/papers/{paper_id}/memory-drafts",
                json={"conversation_id": conversation_id},
                headers=headers,
            )
            assert draft_response.status_code == 200
            draft = draft_response.json()["draft"]
            assert draft["status"] == "draft"

            commit_response = client.post(
                f"/api/memory-drafts/{draft['id']}/commit",
                json={
                    "paper_items": [
                        {
                            "kind": "reading_summary",
                            "content": draft["payload"]["paper_summary"],
                        },
                        {
                            "kind": "key_finding",
                            "content": "确认写入由用户触发。",
                        },
                    ],
                    "accepted_user_items": [],
                },
                headers={
                    **headers,
                    "Idempotency-Key": "api-memory-commit-0001",
                },
            )
            assert commit_response.status_code == 200
            assert len(commit_response.json()["memories"]) == 2

            repeated = client.post(
                f"/api/memory-drafts/{draft['id']}/commit",
                json={"paper_items": [], "accepted_user_items": []},
                headers={
                    **headers,
                    "Idempotency-Key": "api-memory-commit-0001",
                },
            )
            assert repeated.status_code == 200
            assert repeated.json()["memories"] == commit_response.json()["memories"]

            listed = client.get(
                f"/api/papers/{paper_id}/memories",
                headers=headers,
            )
            assert listed.status_code == 200
            assert listed.json()["count"] == 2

            cancelled_draft_response = client.post(
                f"/api/papers/{paper_id}/memory-drafts",
                json={"conversation_id": conversation_id},
                headers=headers,
            )
            assert cancelled_draft_response.status_code == 200
            cancelled_draft = cancelled_draft_response.json()["draft"]
            cancelled = client.post(
                f"/api/memory-drafts/{cancelled_draft['id']}/cancel",
                headers=headers,
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            cancelled_again = client.post(
                f"/api/memory-drafts/{cancelled_draft['id']}/cancel",
                headers=headers,
            )
            assert cancelled_again.status_code == 200
            assert cancelled_again.json()["status"] == "cancelled"
            rejected_commit = client.post(
                f"/api/memory-drafts/{cancelled_draft['id']}/commit",
                json={
                    "paper_items": [
                        {
                            "kind": "reading_summary",
                            "content": "不应写入",
                        }
                    ],
                    "accepted_user_items": [],
                },
                headers={
                    **headers,
                    "Idempotency-Key": "api-cancelled-commit",
                },
            )
            assert rejected_commit.status_code == 409

            manual_memory = client.post(
                "/api/ai/paper-reader/chat",
                json={
                    "paper_id": paper_id,
                    "conversation_id": conversation_id,
                    "messages": [],
                    "user_message": "请记住，我对混合检索感兴趣",
                },
                headers=headers,
            )
            assert manual_memory.status_code == 200
            assert "不会自动写入" in manual_memory.json()["reply"]
            assert client.get(
                f"/api/papers/{paper_id}/memories",
                headers=headers,
            ).json()["count"] == 2

            bob = client.post(
                "/api/auth/register",
                json={"username": "api-bob", "password": "secret2"},
            ).json()
            bob_headers = {"Authorization": f"Bearer {bob['token']}"}
            bob_paper_id, bob_created = database.add_paper(
                Paper(title="Bob API Copy", doi="10.1000/api"),
                user_id=int(bob["user_id"]),
            )
            assert bob_created
            assert bob_paper_id != paper_id
            assert client.get(
                f"/api/papers/{paper_id}",
                headers=bob_headers,
            ).status_code == 404
            assert client.get(
                f"/api/papers/{paper_id}/ingest",
                headers=bob_headers,
            ).status_code == 404
            assert client.get(
                "/api/ai/paper-reader/history",
                params={
                    "paper_id": paper_id,
                    "conversation_id": conversation_id,
                },
                headers=bob_headers,
            ).status_code == 404
            assert client.get(
                f"/api/papers/{paper_id}/memories",
                headers=bob_headers,
            ).json()["count"] == 0
            assert client.post(
                f"/api/memory-drafts/{cancelled_draft['id']}/cancel",
                headers=bob_headers,
            ).status_code == 404

            alice_export = client.get(
                "/api/export/json",
                headers=headers,
            )
            assert alice_export.status_code == 200
            alice_data = alice_export.json()
            assert alice_data["summary"] == {
                "papers": 1,
                "conversations": 1,
                "turns": 5,
                "memories": 2,
                "relations": 0,
                "feedback": 0,
            }
            assert [
                author["name"] for author in alice_data["papers"][0]["authors"]
            ] == ["First Author", "Second Author"]
            assert all(
                memory["confirmed_by_user"]
                for memory in alice_data["memories"]
            )

            bob_export = client.get(
                "/api/export/json",
                headers=bob_headers,
            ).json()
            assert bob_export["summary"]["papers"] == 1
            assert bob_export["summary"]["memories"] == 0
    finally:
        reset_dependencies()
