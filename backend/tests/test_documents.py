"""Provider calls are explicitly mocked; persistence, ownership and quotas use real code."""
import uuid

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from ai.pipeline import PipelineError, StudyPack
from conftest import register_actor
from db import Document, Session
from schemas import Attempt, ScheduleInput
from security import limits
import services


@pytest.mark.parametrize("answers", [[True, 0, 0, 0, 0], ["0", 0, 0, 0, 0], [0.0, 0, 0, 0, 0]])
def test_quiz_rejects_type_coercion(answers):
    with pytest.raises(ValidationError):
        Attempt(answers=answers)


def test_schedule_rejects_boolean_weekday():
    with pytest.raises(ValidationError):
        ScheduleInput(course="Physics", room="A101", weekday=True, start_minute=600, end_minute=690)


async def test_buffer_erased_on_configuration_failure(monkeypatch):
    def unavailable():
        raise PipelineError("AI is not configured")
    monkeypatch.setattr(services, "require_ai", unavailable)
    data = bytearray(b"%PDF-private-pdf")
    with pytest.raises(PipelineError):
        await services.ingest_document(uuid.uuid4(), data, "private.pdf")
    assert data == bytearray()


def study_pack():
    return StudyPack.model_validate({
        "title": "Source mechanics", "summary_markdown": "A source-grounded mechanics summary. " * 4,
        "questions": [{"question": f"Which source statement follows in case {i}?",
                       "choices": ["Force", "Mass", "Time", "Length"], "correct_index": 0,
                       "explanation": "The source identifies force in this case.", "source_chunks": [1]}
                      for i in range(5)],
        "flashcards": [{"front": f"Recall concept {i}", "back": "The answer from the source.", "source_chunks": [1]}
                       for i in range(3)],
    }).model_dump()


async def test_document_tenancy_grading_shared_quota_and_safe_delete(api_client, integration_db, monkeypatch):
    import main
    await register_actor(api_client)
    owner = uuid.UUID((await api_client.get("/api/auth/me")).json()["id"])
    monkeypatch.setattr(services, "require_ai", lambda: None)

    async def generate(*args):
        return study_pack(), [1]
    monkeypatch.setattr(services, "process_pdf", generate)
    response = await api_client.post("/api/ai/generate", content=b"%PDF-provider-fixture", headers={"Content-Type": "application/pdf"})
    assert response.status_code == 201, response.text
    document_id = response.json()["id"]
    path = f"/api/documents/{document_id}"
    assert response.json()["status"] == "ready"
    assert response.json()["pack"]["title"] == "Source mechanics"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=api_client.base_url,
                                 headers={"Origin": str(api_client.base_url).rstrip("/")}) as other:
        await register_actor(other, "other@example.edu")
        assert (await other.get(path)).status_code == 404
        assert (await other.delete(path)).status_code == 404
        assert (await other.post(path + "/ask", json={"question": "What is force?"})).status_code == 404
    answers = {"answers": [0] * 5}
    assert (await api_client.post(path + "/attempts", json={**answers, "score": 5})).status_code == 422
    first = (await api_client.post(path + "/attempts", json=answers)).json()
    second = (await api_client.post(path + "/attempts", json=answers)).json()
    assert first["score"] == 5 and first["xp_awarded"] == 50
    assert second["xp_awarded"] == 0
    assert (await api_client.get("/api/dashboard")).json()["xp"] == 50

    async def ask(*args):
        return {"answer": "The source describes force.", "sources": []}
    monkeypatch.setattr(main, "ask_document", ask)
    assert (await api_client.post(path + "/ask", json={"question": "What is force?"})).status_code == 200
    assert await integration_db.zcard(f"synapse:limit:ai:{owner}") == 2
    for _ in range(8):
        await limits.enforce_rate(f"ai:{owner}", 10, 3600)
    limited = await api_client.post(path + "/ask", json={"question": "What is force?"})
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0

    async def offline(*args):
        raise ConnectionError("Provider offline")
    monkeypatch.setattr(main, "delete_vectors", offline)
    assert (await api_client.delete(path)).status_code == 503
    assert (await api_client.get(path)).status_code == 200
    deleted = []
    async def remove(user_id, doc_id):
        deleted.append((user_id, doc_id))
    monkeypatch.setattr(main, "delete_vectors", remove)
    assert (await api_client.delete(path)).status_code == 204
    assert deleted == [(owner, uuid.UUID(document_id))]
    assert (await api_client.get(path)).status_code == 404


async def test_failed_ingestion_tracks_pending_cleanup_and_erases_buffer(api_client, monkeypatch):
    await register_actor(api_client)
    owner = uuid.UUID((await api_client.get("/api/auth/me")).json()["id"])
    monkeypatch.setattr(services, "require_ai", lambda: None)
    async def bad_pdf(*args):
        raise PipelineError("PDF extraction failed")
    async def offline(*args):
        raise ConnectionError("Provider offline")
    monkeypatch.setattr(services, "process_pdf", bad_pdf)
    monkeypatch.setattr(services, "delete_vectors", offline)
    data = bytearray(b"%PDF-sensitive-content")
    with pytest.raises(PipelineError):
        await services.ingest_document(owner, data, "private.pdf")
    assert not data
    async with Session() as db:
        record = await db.scalar(select(Document))
        assert record.status == "cleanup_pending"
        assert record.payload is None and record.pages is None
