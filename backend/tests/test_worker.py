"""Regression coverage for orphan-cleanup starvation and reconnect/refresh races."""
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import func, select, update

from classroom import worker
from db import Document, GoogleConnection, Session, User, utcnow
from security.auth import decrypt_tokens, encrypt_tokens


async def test_successful_cleanup_is_not_repeated_or_starved(integration_db, monkeypatch):
    owner = uuid.uuid4()
    async with Session() as db:
        db.add(User(id=owner, email="worker@example.edu", name="Worker Student", password_hash="unused-test-hash"))
        await db.flush()
        db.add_all([Document(user_id=owner, filename=f"pending-{i}.pdf", status="cleanup_pending",
                             created_at=utcnow() - timedelta(days=1)) for i in range(25)])
        db.add(Document(user_id=owner, filename="already-cleaned.pdf", status="failed",
                        created_at=utcnow() - timedelta(days=2)))
        await db.commit()
    removed = []
    async def delete_vectors(user_id, document_id):
        assert user_id == owner
        removed.append(document_id)
    monkeypatch.setattr(worker, "delete_vectors", delete_vectors)
    await worker.cleanup()
    assert len(removed) == 20
    await worker.cleanup()
    assert len(removed) == len(set(removed)) == 25
    await worker.cleanup()
    assert len(removed) == 25
    async with Session() as db:
        assert await db.scalar(select(func.count()).select_from(Document).where(Document.status == "failed")) == 26


async def test_refresh_cannot_overwrite_a_new_google_grant(integration_db, monkeypatch):
    owner = uuid.uuid4()
    old = encrypt_tokens({"access_token": "old-access", "refresh_token": "old-refresh", "expires_at": 0}, owner)
    replacement = encrypt_tokens({"access_token": "replacement-access", "refresh_token": "replacement-refresh",
                                  "expires_at": utcnow().timestamp() + 3600}, owner)
    async with Session() as db:
        db.add(User(id=owner, email="reconnect@example.edu", name="Reconnect Student", password_hash="unused-test-hash"))
        await db.flush()
        db.add(GoogleConnection(user_id=owner, credentials=old))
        await db.commit()
    calls = []
    async def endpoint(request):
        calls.append(str(request.url))
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        # Simulate OAuth reconnect finishing while the old refresh request is in flight.
        async with Session() as db:
            await db.execute(update(GoogleConnection).where(GoogleConnection.user_id == owner).values(credentials=replacement))
            await db.commit()
        return httpx.Response(200, json={"access_token": "stale-refreshed-access", "expires_in": 3600})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(worker.httpx, "AsyncClient", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(endpoint)))
    await worker.sync_user(owner)
    assert calls == ["https://oauth2.googleapis.com/token"]
    async with Session() as db:
        connection = await db.get(GoogleConnection, owner)
        assert connection.credentials == replacement
        assert decrypt_tokens(connection.credentials, owner)["refresh_token"] == "replacement-refresh"
