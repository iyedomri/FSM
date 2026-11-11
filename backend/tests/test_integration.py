"""Actual PostgreSQL migrations and API contracts; only a disposable synapse_test DB."""
import asyncio
from datetime import timedelta

import httpx
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from config import settings
from conftest import register_actor
from db import Base, Session, StudySession, engine, utcnow


async def test_migrations_match_models(integration_db):
    async with engine.connect() as connection:
        differences = await connection.run_sync(lambda sync: compare_metadata(
            MigrationContext.configure(sync, opts={"compare_type": True}), Base.metadata))
    assert differences == []


async def test_auth_ownership_schedule_progress_and_replay(api_client):
    from main import app
    client = api_client
    response = await register_actor(client)
    cookie = client.cookies.get(settings.refresh_cookie)
    assert "HttpOnly" in response.headers["set-cookie"]
    slot = {"course": "Physics", "room": "A101", "weekday": 0, "start_minute": 600, "end_minute": 690}
    response = await client.post("/api/schedule", json=slot)
    assert response.status_code == 201, response.text
    slot_id = response.json()["id"]
    assert (await client.post("/api/schedule", json=slot)).status_code == 409
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_origin,
                                 headers={"Origin": settings.public_origin}) as other:
        await register_actor(other, "other@example.edu")
        assert (await other.get("/api/schedule")).json() == []
        assert (await other.delete(f"/api/schedule/{slot_id}")).status_code == 404
    started = (await client.post("/api/study/start")).json()
    async with Session() as db:
        item = await db.scalar(select(StudySession))
        item.started_at = utcnow() - timedelta(seconds=75)
        await db.commit()
    stopped = await client.post(f"/api/study/{started['id']}/stop")
    assert stopped.json()["seconds"] >= 75
    assert (await client.get("/api/dashboard")).json()["today_minutes"] == 1
    refreshed = await client.post("/api/auth/refresh")
    assert refreshed.status_code == 200
    successor = client.cookies.get(settings.refresh_cookie)
    assert successor != cookie
    replay = await client.post("/api/auth/refresh", headers={"Cookie": f"{settings.refresh_cookie}={cookie}"})
    assert replay.status_code == 401
    assert (await client.post("/api/auth/refresh", headers={"Cookie": f"{settings.refresh_cookie}={successor}"})).status_code == 401


async def test_concurrent_rotation_detects_replay(api_client):
    await register_actor(api_client)
    raw = api_client.cookies.get(settings.refresh_cookie)
    responses = await asyncio.gather(*(
        api_client.post("/api/auth/refresh", headers={"Cookie": f"{settings.refresh_cookie}={raw}"})
        for _ in range(2)
    ))
    assert sorted(response.status_code for response in responses) == [200, 401]
    successful = next(response for response in responses if response.status_code == 200)
    replacement = successful.cookies.get(settings.refresh_cookie)
    replay = await api_client.post("/api/auth/refresh", headers={"Cookie": f"{settings.refresh_cookie}={replacement}"})
    assert replay.status_code == 401
