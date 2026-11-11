import base64
import os
from pathlib import Path
from urllib.parse import urlsplit

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

os.environ.update(ENVIRONMENT="test", PUBLIC_ORIGIN="http://localhost:3000", SECURE_COOKIES="false",
                  DATABASE_URL=os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/synapse_test"),
                  REDIS_URL="redis://localhost:6379/15", JWT_SECRET="x" * 86,
                  TOKEN_ENCRYPTION_KEY=base64.b64encode(b"k" * 32).decode(), REGISTRATION_INVITE="private-test-invitation-12345678",
                  OPENAI_API_KEY="", PINECONE_API_KEY="", GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="",
                  LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")


@pytest_asyncio.fixture
async def integration_db(monkeypatch):
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("Set TEST_DATABASE_URL to a disposable /synapse_test PostgreSQL database")
    from config import settings
    from db import engine
    from security import limits

    if urlsplit(settings.database_url).path != "/synapse_test":
        raise RuntimeError("Refusing to reset a database not named synapse_test")
    await engine.dispose()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(limits, "redis", fake)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))

    def migrate(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    try:
        async with engine.begin() as connection:
            # The explicit database-name guard above is intentionally not an assert.
            await connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            await connection.exec_driver_sql("CREATE SCHEMA public")
            await connection.run_sync(migrate)
        yield fake
    finally:
        await fake.aclose()
        await engine.dispose()


@pytest_asyncio.fixture
async def api_client(integration_db):
    from config import settings
    from main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url=settings.public_origin,
                                 headers={"Origin": settings.public_origin}) as client:
        yield client


async def register_actor(client, email="student@example.edu"):
    from config import settings

    response = await client.post("/api/auth/register", json={
        "email": email, "name": "FSM Student", "password": "long-test-password",
        "invite_code": settings.registration_invite.get_secret_value(),
    })
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return response
