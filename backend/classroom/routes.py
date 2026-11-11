import base64
import hashlib
import hmac
import secrets
import uuid
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from redis.exceptions import RedisError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import GoogleConnection, get_db, utcnow
from security.auth import clear_oauth_cookie, current_user, encrypt_tokens, token_hash
from security.limits import enforce_rate, redis

router = APIRouter(prefix="/api/integrations/google", tags=["classroom"])
SCOPES = ["https://www.googleapis.com/auth/classroom.courses.readonly",
          "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
          "https://www.googleapis.com/auth/drive.readonly"]


def validated_grant(payload):
    """Store only a complete, usable offline grant; provider JSON is not implicitly trusted."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid Google grant")
    for key in ("access_token", "refresh_token"):
        value = payload.get(key)
        if not isinstance(value, str) or not 1 <= len(value) <= 16384 or any(not 33 <= ord(c) <= 126 for c in value):
            raise ValueError("Offline consent required")
    lifetime, scope = payload.get("expires_in"), payload.get("scope")
    if type(lifetime) is not int or not 1 <= lifetime <= 86400:
        raise ValueError("Invalid Google token lifetime")
    if not isinstance(scope, str) or not set(SCOPES).issubset(scope.split()):
        raise ValueError("All requested read-only permissions are required")
    kind = payload.get("token_type")
    if not isinstance(kind, str) or kind.casefold() != "bearer":
        raise ValueError("Invalid Google token type")
    return {"access_token": payload["access_token"], "refresh_token": payload["refresh_token"],
            "expires_in": lifetime, "expires_at": utcnow().timestamp() + lifetime,
            "scope": scope, "token_type": "Bearer"}


@router.get("/status")
async def status(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    connection = await db.get(GoogleConnection, user.id)
    return {"configured": bool(settings.google_client_id and settings.google_client_secret.get_secret_value()),
            "connected": bool(connection), "last_sync_at": connection.last_sync_at.isoformat() if connection and connection.last_sync_at else None,
            "error": connection.last_error if connection else None,
            "syncing": bool(connection and connection.lease_until and connection.lease_until > utcnow())}


@router.post("/connect")
async def connect(user=Depends(current_user)):
    if not settings.google_client_id or not settings.google_client_secret.get_secret_value():
        raise HTTPException(503, "Google OAuth is not configured")
    await enforce_rate(f"google-connect:{user.id}", 5, 300)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    try:
        await redis.set("synapse:oauth:" + token_hash(state), f"{user.id}|{verifier}", ex=600)
    except RedisError:
        raise HTTPException(503, "Google connection state unavailable; retry later") from None
    params = {"client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri,
              "response_type": "code", "scope": " ".join(SCOPES), "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"}
    response = JSONResponse({"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)})
    # __Host- requires Secure, no Domain and Path=/; sibling subdomains cannot plant this cookie.
    response.set_cookie(settings.oauth_state_cookie, state, max_age=600, httponly=True, secure=settings.secure_cookies,
                        samesite="lax", path="/")
    return response


@router.get("/callback")
async def callback(request: Request, db: AsyncSession = Depends(get_db)):
    state = request.query_params.get("state", "")
    cookie = request.cookies.get(settings.oauth_state_cookie, "")
    if not state or len(state) > 128 or not cookie or not hmac.compare_digest(state.encode(), cookie.encode()):
        raise HTTPException(400, "Invalid OAuth state")
    try:
        saved = await redis.getdel("synapse:oauth:" + token_hash(state))
    except RedisError:
        raise HTTPException(503, "Google connection state unavailable; retry later") from None
    if not saved:
        raise HTTPException(400, "OAuth state expired or already used")
    outcome = "failed"
    if request.query_params.get("code") and not request.query_params.get("error"):
        try:
            user_id, verifier = saved.split("|", 1)
            user_id = uuid.UUID(user_id)
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                result = await client.post("https://oauth2.googleapis.com/token", data={
                    "code": request.query_params["code"], "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret.get_secret_value(), "grant_type": "authorization_code",
                    "redirect_uri": settings.google_redirect_uri, "code_verifier": verifier})
                result.raise_for_status()
                tokens = validated_grant(result.json())
            encrypted = encrypt_tokens(tokens, user_id)
            statement = insert(GoogleConnection).values(user_id=user_id, credentials=encrypted, next_sync_at=utcnow())
            await db.execute(statement.on_conflict_do_update(index_elements=[GoogleConnection.user_id], set_={
                "credentials": statement.excluded.credentials, "next_sync_at": statement.excluded.next_sync_at,
                "last_error": None,
            }))  # Concurrent first-time callbacks cannot race a read-then-insert.
            await db.commit()
            outcome = "connected"
        except (httpx.HTTPError, ValueError, KeyError):
            await db.rollback()
    response = RedirectResponse(settings.public_origin + "/?google=" + outcome, status_code=303)
    clear_oauth_cookie(response)
    return response


@router.post("/sync", status_code=202)
async def queue_sync(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    await enforce_rate(f"google-sync:{user.id}", 5, 300)
    connection = await db.get(GoogleConnection, user.id)
    if not connection:
        raise HTTPException(409, "Connect Google Classroom first")
    connection.next_sync_at = utcnow()
    await db.commit()
    return {"status": "queued"}


@router.delete("/disconnect", status_code=204)
async def disconnect(response: Response, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    connection = await db.get(GoogleConnection, user.id)
    if connection:
        # Delete credentials first; already generated lessons remain under the student's control.
        await db.delete(connection)
        await db.commit()
    clear_oauth_cookie(response)
