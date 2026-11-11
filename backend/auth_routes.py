import asyncio
import hmac
import ipaddress
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import RefreshToken, User, get_db, utcnow
from schemas import Login, Register
from security.auth import (DUMMY_HASH, clear_oauth_cookie, clear_refresh_cookie, current_user,
                           issue_access, passwords, set_refresh_cookie, token_hash)
from security.limits import enforce_rate

password_slots = asyncio.Semaphore(2)


async def hash_password(value):
    async with password_slots:
        return await asyncio.to_thread(passwords.hash, value)


async def verify_password(value, hashed):
    async with password_slots:
        return await asyncio.to_thread(passwords.verify, value, hashed)


router = APIRouter(prefix="/api/auth", tags=["authentication"])


def public_user(user):
    return {"id": str(user.id), "name": user.name, "email": user.email}


def new_refresh(db, user_id, family_id=None, expires_at=None):
    raw = secrets.token_urlsafe(48)
    expires_at = expires_at or utcnow() + timedelta(days=30)
    db.add(RefreshToken(user_id=user_id, family_id=family_id or uuid.uuid4(),
                        token_hash=token_hash(raw), expires_at=expires_at))
    return raw, expires_at


def client_ip(request):
    if settings.environment == "production":
        # Caddy overwrites this header. The frontend must not be exposed directly.
        try:
            return str(ipaddress.ip_address(request.headers.get("x-synapse-client-ip", "")))
        except ValueError:
            raise HTTPException(400, "A trusted reverse proxy is required") from None
    return request.client.host if request.client else "unknown"


async def login_limit(request, email):
    # Account-based throttling is effective even behind the same-origin reverse proxy.
    await enforce_rate("login-account:" + token_hash(email), 8, 900)
    await enforce_rate("login-proxy:" + client_ip(request), 300, 900)


@router.post("/register", status_code=201)
async def register(body: Register, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    await login_limit(request, body.email)
    await enforce_rate("register-proxy:" + client_ip(request), 10, 3600)
    if not hmac.compare_digest(body.invite_code.encode(), settings.registration_invite.get_secret_value().encode()):
        raise HTTPException(403, "Invalid FSM Club access code")
    user = User(email=body.email, name=body.name, password_hash=await hash_password(body.password))
    db.add(user)
    try:
        await db.flush()
        raw, expiry = new_refresh(db, user.id)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This email cannot be registered") from None
    set_refresh_cookie(response, raw, expiry)
    clear_oauth_cookie(response)
    return {"access_token": issue_access(user.id), "user": public_user(user)}


@router.post("/login")
async def login(body: Login, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    await login_limit(request, body.email)
    user = await db.scalar(select(User).where(User.email == body.email))
    valid = await verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid:
        raise HTTPException(401, "Invalid email or password")
    raw, expiry = new_refresh(db, user.id)
    await db.commit()
    set_refresh_cookie(response, raw, expiry)
    clear_oauth_cookie(response)
    return {"access_token": issue_access(user.id), "user": public_user(user)}


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get(settings.refresh_cookie, "")
    if not raw or len(raw) > 256:
        raise HTTPException(401, "Session expired")
    await enforce_rate("refresh:" + token_hash(raw), 20, 60)
    record = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw)))
    if not record:
        raise HTTPException(401, "Session expired")
    # One lock per account serializes rotation, replay revocation and logout across a family.
    user = await db.scalar(select(User).where(User.id == record.user_id).with_for_update())
    await db.refresh(record, with_for_update=True)
    if record.used_at or record.revoked_at or record.expires_at <= utcnow():
        await db.execute(update(RefreshToken).where(RefreshToken.family_id == record.family_id).values(revoked_at=utcnow()))
        await db.commit()  # Persist family revocation even though the response is an error.
        error = JSONResponse({"detail": "Session expired or refresh token reused"}, 401)
        clear_refresh_cookie(error)
        return error
    if not user:
        raise HTTPException(401, "Session expired")
    record.used_at = utcnow()
    replacement, expiry = new_refresh(db, user.id, record.family_id, record.expires_at)
    await db.commit()
    set_refresh_cookie(response, replacement, expiry)
    return {"access_token": issue_access(user.id), "user": public_user(user)}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get(settings.refresh_cookie, "")
    record = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw))) if raw else None
    if record:
        await db.scalar(select(User.id).where(User.id == record.user_id).with_for_update())
        await db.execute(update(RefreshToken).where(RefreshToken.family_id == record.family_id).values(revoked_at=utcnow()))
        await db.commit()
    clear_refresh_cookie(response)
    clear_oauth_cookie(response)


@router.get("/me")
async def me(user=Depends(current_user)):
    return public_user(user)
