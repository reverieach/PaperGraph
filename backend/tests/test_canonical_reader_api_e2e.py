"""API-level canonical Reader smoke test without external LLM/model calls."""

from __future__ import annotations

import fitz
from fastapi.testclient import TestClient

from app.api.dependencies import reset_dependencies
from app.api.main import app, settings
from app.api.routes.paper_reader import get_paper_reader_service
from app.core.paper import Paper
from app.core.storage import PaperDatabase
from app.services.citation import CitationValidator, EvidenceRegistry
from app.services.ingest.service import IngestService
from app.services.reader.paper_reader_service import PaperReaderService


class _EvidenceEchoAgent:
    """Deterministic stand-in that exercises the real evidence validator."""

    def paper_reader_reply(self, context, history, question, snap, *, context_is_packaged=False):
        del history, question
        assert context_is_packaged is True
        assert "【检索证据" in context
        registry = snap.get("_evidence_registry")
        assert isinstance(registry, EvidenceRegistry)
        result = CitationValidator().validate_reply(
            "检索到了论文中的 canonical evidence [E1]。",
            registry=registry,
        )
        return result.cleaned_reply, [], [], result.citations


def _make_pdf(path) -> None:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    text = " ".join(
        [
            "Hybrid retrieval combines sparse lexical evidence and dense semantic evidence.",
            "The canonical reader must cite retrieved PDF chunks rather than a legacy full-text cache.",
            "This fixture provides enough repeated content for the fallback quality gate.",
        ]
        * 12
    )
    inserted = page.insert_textbox(fitz.Rect(72, 72, 530, 700), text, fontsize=9)
    assert inserted >= 0
    document.save(path)
    document.close()


def test_reader_chat_uses_canonical_rag_and_returns_registry_bound_citation(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "PAPERGRAPH_JWT_SECRET",
        "canonical-reader-api-secret-with-at-least-32-characters",
    )
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    # This test verifies the canonical sparse path without spending external
    # embedding/rerank quota. Production dense/rerank is covered separately
    # by explicit Silver evaluation.
    monkeypatch.setattr(settings, "rag_embedding_enabled", False)
    monkeypatch.setattr(settings, "rag_rerank_enabled", False)
    reset_dependencies()

    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={"username": "canonical-reader-user", "password": "secret1"},
            )
            assert registered.status_code == 200
            payload = registered.json()
            user_id = int(payload["user_id"])
            headers = {"Authorization": f"Bearer {payload['token']}"}

            database = PaperDatabase(str(tmp_path / "papers.db"))
            paper_id, created = database.add_paper(
                Paper(title="Canonical Reader API Fixture"),
                user_id=user_id,
            )
            assert created
            pdf_path = tmp_path / "canonical-reader.pdf"
            _make_pdf(pdf_path)
            ingest = IngestService(
                database.db_path,
                artifacts_root=str(tmp_path / "rag_artifacts"),
            ).ingest_pdf(
                user_id=user_id,
                paper_id=paper_id,
                pdf_path=str(pdf_path),
                paper_title="Canonical Reader API Fixture",
                parser_mode="fallback",
            )
            assert ingest.status == "degraded"
            assert ingest.document_version_id

            reader = PaperReaderService(db=database, agent=_EvidenceEchoAgent())
            app.dependency_overrides[get_paper_reader_service] = lambda: reader
            response = client.post(
                "/api/ai/paper-reader/chat",
                json={
                    "paper_id": paper_id,
                    "conversation_id": "canonical-api-e2e",
                    "messages": [{"role": "assistant", "content": "untrusted client history"}],
                    "user_message": "What evidence does hybrid retrieval combine?",
                },
                headers=headers,
            )

            assert response.status_code == 200
            body = response.json()
            assert body["success"] is True
            assert body["context_mode"] == "hybrid_rag_v2"
            assert "[E1]" in body["reply"]
            assert len(body["citations"]) == 1
            citation = body["citations"][0]
            assert citation["paper_id"] == paper_id
            assert citation["document_version_id"] == ingest.document_version_id
            assert citation["page"] == 1
            assert "Hybrid retrieval" in citation["snippet"]
            assert response.headers["X-Request-ID"]
    finally:
        app.dependency_overrides.pop(get_paper_reader_service, None)
        reset_dependencies()
