from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.dependencies import reset_dependencies
from app.api.main import app, settings
from app.core.paper import Paper
from app.core.storage import PaperDatabase
from app.services.research.multi_paper_service import MultiPaperResearchService


class _ResearchLLM:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def chat(self, messages, **kwargs):
        del kwargs
        self.messages = list(messages)
        return SimpleNamespace(content="两篇论文都研究检索，但方法和评测目标不同。")


def test_manual_user_memory_and_research_session_are_user_scoped(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "PAPERGRAPH_JWT_SECRET",
        "memory-research-test-secret-with-at-least-32-chars",
    )
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    fake_llm = _ResearchLLM()
    monkeypatch.setattr(
        MultiPaperResearchService,
        "_get_llm",
        lambda self: fake_llm,
    )
    reset_dependencies()

    try:
        with TestClient(app) as client:
            alice = client.post(
                "/api/auth/register",
                json={"username": "research-alice", "password": "secret1"},
            ).json()
            bob = client.post(
                "/api/auth/register",
                json={"username": "research-bob", "password": "secret2"},
            ).json()
            alice_headers = {"Authorization": f"Bearer {alice['token']}"}
            bob_headers = {"Authorization": f"Bearer {bob['token']}"}

            created_memory = client.post(
                "/api/memory/user",
                json={
                    "kind": "research_goal",
                    "content": "  比较   混合检索方法  ",
                },
                headers=alice_headers,
            )
            assert created_memory.status_code == 200
            assert created_memory.json()["created"] is True
            memory = created_memory.json()["item"]
            assert memory["scope_type"] == "user"
            assert memory["scope_id"] == str(alice["user_id"])
            assert memory["content"] == "比较 混合检索方法"
            assert memory["confirmed_by_user"] is True

            duplicate = client.post(
                "/api/memory/user",
                json={
                    "kind": "research_goal",
                    "content": "比较 混合检索方法",
                },
                headers=alice_headers,
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["created"] is False
            assert duplicate.json()["item"]["id"] == memory["id"]

            assert client.get(
                "/api/memory/user",
                headers=alice_headers,
            ).json()["count"] == 1
            assert client.get(
                "/api/memory/user",
                headers=bob_headers,
            ).json()["count"] == 0
            assert client.delete(
                f"/api/memories/{memory['id']}",
                headers=bob_headers,
            ).status_code == 404

            database = PaperDatabase(str(tmp_path / "papers.db"))
            first_id, _ = database.add_paper(
                Paper(
                    title="Hybrid Retrieval",
                    abstract="A hybrid sparse and dense retrieval method.",
                    year=2025,
                    category="RAG",
                ),
                user_id=int(alice["user_id"]),
            )
            second_id, _ = database.add_paper(
                Paper(
                    title="Agentic Search",
                    abstract="An agent plans queries and evaluates evidence.",
                    year=2026,
                    category="Agent",
                ),
                user_id=int(alice["user_id"]),
            )
            bob_paper_id, _ = database.add_paper(
                Paper(title="Bob Private Paper", abstract="private"),
                user_id=int(bob["user_id"]),
            )

            cross_user_create = client.post(
                "/api/research/sessions",
                json={"paper_ids": [first_id, bob_paper_id]},
                headers=alice_headers,
            )
            assert cross_user_create.status_code == 404

            created_session = client.post(
                "/api/research/sessions",
                json={"paper_ids": [first_id, second_id, first_id]},
                headers=alice_headers,
            )
            assert created_session.status_code == 200
            session = created_session.json()["session"]
            assert [paper["id"] for paper in session["papers"]] == [
                first_id,
                second_id,
            ]

            chatted = client.post(
                f"/api/research/sessions/{session['id']}/chat",
                json={"user_message": "比较两篇论文的方法"},
                headers=alice_headers,
            )
            assert chatted.status_code == 200
            assert chatted.json()["context_mode"] == "metadata_abstract_v1"
            assert len(chatted.json()["turns"]) == 2
            system_context = "\n".join(
                item["content"]
                for item in fake_llm.messages
                if item["role"] == "system"
            )
            assert "Hybrid Retrieval" in system_context
            assert "Agentic Search" in system_context
            assert "不得虚构页码" in system_context

            restored = client.get(
                f"/api/research/sessions/{session['id']}",
                headers=alice_headers,
            )
            assert restored.status_code == 200
            assert len(restored.json()["session"]["turns"]) == 2
            assert client.get(
                f"/api/research/sessions/{session['id']}",
                headers=bob_headers,
            ).status_code == 404

            deleted = client.delete(
                f"/api/memories/{memory['id']}",
                headers=alice_headers,
            )
            assert deleted.status_code == 200
            assert client.get(
                "/api/memory/user",
                headers=alice_headers,
            ).json()["count"] == 0
    finally:
        reset_dependencies()
