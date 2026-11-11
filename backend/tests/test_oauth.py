"""Real HTTP/SQL/Redis-state contracts; Google exchanges use an explicit MockTransport."""
import asyncio
import base64
import hashlib
import uuid
from datetime import timedelta
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from classroom import routes
from config import settings
from conftest import register_actor
from db import GoogleConnection, Session, utcnow
from security.auth import decrypt_tokens, encrypt_tokens, token_hash


def grant(**changes):
    return {"access_token": "private-google-access", "refresh_token": "private-google-refresh",
            "expires_in": 3600, "token_type": "Bearer", "scope": " ".join(routes.SCOPES), **changes}


@pytest.fixture
async def oauth(api_client, integration_db, monkeypatch):
    registration = await register_actor(api_client)
    user_id = uuid.UUID(registration.json()["user"]["id"])
    monkeypatch.setattr(settings, "google_client_id", "test-google-client")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-google-secret"))
    monkeypatch.setattr(routes, "redis", integration_db)
    requests = []
    provider = SimpleNamespace(payload=grant(), status_code=200)

    async def exchange(request):
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        requests.append(request)
        return httpx.Response(provider.status_code, json=provider.payload)

    real_client = httpx.AsyncClient
    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(exchange), **kwargs)

    # Replace only this module's reference, never the HTTP client used by the ASGI fixture.
    monkeypatch.setattr(routes, "httpx", SimpleNamespace(AsyncClient=client_factory, HTTPError=httpx.HTTPError))
    return SimpleNamespace(client=api_client, redis=integration_db, user_id=user_id,
                           provider=provider, requests=requests)


async def begin(oauth):
    response = await oauth.client.post("/api/integrations/google/connect")
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["url"])
    params = {key: values[0] for key, values in parse_qs(url.query).items()}
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert len(cookies) == 1
    cookie_name = next(iter(cookies))
    return response, url, params, cookie_name


async def finish(oauth, params, **changes):
    return await oauth.client.get("/api/integrations/google/callback",
                                  params={"state": params["state"], "code": "test-auth-code", **changes})


async def test_google_is_explicitly_unconfigured_without_keys(api_client):
    await register_actor(api_client)
    status = await api_client.get("/api/integrations/google/status")
    assert status.json()["configured"] is False and status.json()["connected"] is False
    assert (await api_client.post("/api/integrations/google/connect")).status_code == 503


async def test_connect_binds_browser_and_user_with_hashed_single_use_pkce(oauth):
    response, url, params, name = await begin(oauth)
    assert url.scheme == "https" and url.netloc == "accounts.google.com"
    assert params["client_id"] == "test-google-client"
    assert params["redirect_uri"] == settings.google_redirect_uri
    assert set(params["scope"].split()) == set(routes.SCOPES)
    assert params["code_challenge_method"] == "S256"
    assert params["access_type"] == "offline" and params["prompt"] == "consent"
    state = params["state"]
    stored = await oauth.redis.get("synapse:oauth:" + token_hash(state))
    owner, verifier = stored.split("|", 1)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert uuid.UUID(owner) == oauth.user_id and expected == params["code_challenge"]
    assert verifier not in response.text and "test-google-secret" not in response.text
    assert await oauth.redis.get("synapse:oauth:" + state) is None
    assert 0 < await oauth.redis.ttl("synapse:oauth:" + token_hash(state)) <= 600
    cookie = SimpleCookie(response.headers["set-cookie"])[name]
    assert cookie["httponly"] and cookie["samesite"].lower() == "lax"
    assert cookie["path"] == "/" and cookie["max-age"] == "600"


async def test_secure_oauth_state_cookie_cannot_be_planted_by_a_sibling_host(oauth, monkeypatch):
    monkeypatch.setattr(settings, "secure_cookies", True)
    response, _, _, name = await begin(oauth)
    cookie = SimpleCookie(response.headers["set-cookie"])[name]
    assert name == "__Host-synapse-oauth-state"
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["path"] == "/" and not cookie["domain"]


@pytest.mark.parametrize("state", ["wrong-state", "\u00e9", "x" * 129])
async def test_bad_state_never_consumes_a_valid_flow_or_calls_google(oauth, state):
    _, _, params, _ = await begin(oauth)
    response = await finish(oauth, params, state=state)
    assert response.status_code == 400
    assert await oauth.redis.exists("synapse:oauth:" + token_hash(params["state"]))
    assert oauth.requests == []


async def test_oauth_callback_requires_the_initiating_browser_cookie(oauth):
    _, _, params, _ = await begin(oauth)
    oauth.client.cookies.clear()
    assert (await finish(oauth, params)).status_code == 400
    assert oauth.requests == []


async def test_callback_encrypts_tokens_queues_sync_and_rejects_replay(oauth):
    _, _, params, name = await begin(oauth)
    response = await finish(oauth, params)
    assert response.status_code == 303
    assert response.headers["location"] == settings.public_origin + "/?google=connected"
    assert oauth.client.cookies.get(name) is None
    assert await oauth.redis.get("synapse:oauth:" + token_hash(params["state"])) is None
    assert len(oauth.requests) == 1
    sent = parse_qs(oauth.requests[0].content.decode())
    assert sent["grant_type"] == ["authorization_code"] and sent["code"] == ["test-auth-code"]
    assert sent["redirect_uri"] == [settings.google_redirect_uri]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
    assert challenge == params["code_challenge"]
    async with Session() as db:
        connection = await db.get(GoogleConnection, oauth.user_id)
        assert "private-google" not in connection.credentials
        tokens = decrypt_tokens(connection.credentials, oauth.user_id)
        assert tokens["refresh_token"] == "private-google-refresh"
        assert 3500 < tokens["expires_at"] - utcnow().timestamp() <= 3600
        assert connection.next_sync_at <= utcnow()
    replay = await oauth.client.get("/api/integrations/google/callback", params=params,
                                   headers={"Cookie": f"{name}={params['state']}"})
    assert replay.status_code == 400 and len(oauth.requests) == 1


async def test_consent_denial_consumes_state_without_storing_a_grant(oauth):
    _, _, params, name = await begin(oauth)
    response = await finish(oauth, params, error="access_denied")
    assert response.status_code == 303 and response.headers["location"].endswith("?google=failed")
    assert oauth.client.cookies.get(name) is None
    assert await oauth.redis.get("synapse:oauth:" + token_hash(params["state"])) is None
    assert oauth.requests == []
    async with Session() as db:
        assert await db.get(GoogleConnection, oauth.user_id) is None


@pytest.mark.parametrize("payload", [None, [], grant(refresh_token=None), grant(access_token=42),
                                     grant(expires_in=None), grant(expires_in=True), grant(expires_in=-1),
                                     grant(token_type="Other"), grant(scope=routes.SCOPES[0])])
async def test_invalid_or_partial_provider_grants_fail_closed(oauth, payload):
    oauth.provider.payload = payload
    _, _, params, name = await begin(oauth)
    response = await finish(oauth, params)
    assert response.status_code == 303 and response.headers["location"].endswith("?google=failed")
    assert oauth.client.cookies.get(name) is None
    assert await oauth.redis.get("synapse:oauth:" + token_hash(params["state"])) is None
    async with Session() as db:
        assert await db.get(GoogleConnection, oauth.user_id) is None


async def test_provider_http_error_preserves_the_previous_encrypted_grant(oauth):
    previous = encrypt_tokens(grant(), oauth.user_id)
    async with Session() as db:
        db.add(GoogleConnection(user_id=oauth.user_id, credentials=previous))
        await db.commit()
    oauth.provider.status_code = 400
    oauth.provider.payload = {"error": "invalid_grant"}
    _, _, params, _ = await begin(oauth)
    response = await finish(oauth, params)
    assert response.status_code == 303 and response.headers["location"].endswith("?google=failed")
    async with Session() as db:
        assert (await db.get(GoogleConnection, oauth.user_id)).credentials == previous


@pytest.mark.parametrize("method", ["set", "getdel"])
async def test_unavailable_state_store_returns_retryable_error_without_an_exchange(oauth, monkeypatch, method):
    params = None
    if method == "getdel":
        _, _, params, _ = await begin(oauth)
    monkeypatch.setattr(oauth.redis, method, AsyncMock(side_effect=RedisConnectionError("offline")))
    response = await finish(oauth, params) if params else await oauth.client.post("/api/integrations/google/connect")
    assert response.status_code == 503 and oauth.requests == []


@pytest.mark.parametrize("transition", ["login", "register", "logout", "disconnect"])
async def test_account_transitions_remove_pending_browser_consent(oauth, transition):
    _, _, params, name = await begin(oauth)
    if transition == "login":
        response = await oauth.client.post("/api/auth/login", json={"email": "student@example.edu", "password": "long-test-password"})
    elif transition == "register":
        response = await register_actor(oauth.client, "new-student@example.edu")
    elif transition == "disconnect":
        response = await oauth.client.delete("/api/integrations/google/disconnect")
    else:
        response = await oauth.client.post("/api/auth/logout")
    assert response.status_code in {200, 201, 204}
    assert oauth.client.cookies.get(name) is None
    assert (await finish(oauth, params)).status_code == 400
    assert oauth.requests == []


async def test_parallel_first_time_callbacks_upsert_a_single_connection(oauth, monkeypatch):
    # Force the old read-then-insert implementation to observe the same absent row.
    # An atomic upsert has no pre-insert read and needs no such synchronization.
    original_get = AsyncSession.get
    arrived, release = 0, asyncio.Event()
    async def synchronized_get(self, entity, *args, **kwargs):
        nonlocal arrived
        result = await original_get(self, entity, *args, **kwargs)
        if entity is GoogleConnection and result is None:
            arrived += 1
            if arrived == 2:
                release.set()
            await asyncio.wait_for(release.wait(), 5)
        return result
    monkeypatch.setattr(AsyncSession, "get", synchronized_get)
    _, _, first, first_name = await begin(oauth)
    _, _, second, second_name = await begin(oauth)
    async def callback(params, name):
        return await oauth.client.get("/api/integrations/google/callback", params={"state": params["state"], "code": "parallel-code"},
                                      headers={"Cookie": f"{name}={params['state']}"})
    results = await asyncio.gather(callback(first, first_name), callback(second, second_name), return_exceptions=True)
    assert all(isinstance(result, httpx.Response) and result.status_code == 303 and
               result.headers["location"].endswith("?google=connected") for result in results), results
    async with Session() as db:
        assert await db.scalar(select(func.count()).select_from(GoogleConnection)) == 1


async def test_reconnect_replaces_credentials_and_disconnect_is_idempotent(oauth):
    previous = encrypt_tokens(grant(access_token="older-access"), oauth.user_id)
    async with Session() as db:
        db.add(GoogleConnection(user_id=oauth.user_id, credentials=previous,
                                next_sync_at=utcnow() + timedelta(hours=1), last_error="Expired grant"))
        await db.commit()
    _, _, params, _ = await begin(oauth)
    response = await finish(oauth, params)
    assert response.headers["location"].endswith("?google=connected")
    async with Session() as db:
        row = await db.get(GoogleConnection, oauth.user_id)
        assert row.credentials != previous and row.last_error is None and row.next_sync_at <= utcnow()
    assert (await oauth.client.post("/api/integrations/google/sync")).status_code == 202
    assert (await oauth.client.delete("/api/integrations/google/disconnect")).status_code == 204
    assert (await oauth.client.delete("/api/integrations/google/disconnect")).status_code == 204
    assert (await oauth.client.post("/api/integrations/google/sync")).status_code == 409
