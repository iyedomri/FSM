import base64
import hashlib
import json
import os
import secrets
import uuid
from datetime import timedelta

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

from config import settings
from db import Session, User, utcnow

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))
bearer = HTTPBearer(auto_error=False)


def issue_access(user_id: uuid.UUID) -> str:
    now = utcnow()
    return jwt.encode({"sub": str(user_id), "iss": "synapse", "aud": "synapse-web", "typ": "access",
                       "iat": now, "nbf": now, "exp": now + timedelta(minutes=15), "jti": str(uuid.uuid4())},
                      settings.jwt_secret.get_secret_value(), algorithm="HS256")


def decode_access(token: str) -> uuid.UUID:
    claims = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"],
                        audience="synapse-web", issuer="synapse",
                        options={"require": ["sub", "exp", "iat", "nbf", "iss", "aud", "typ", "jti"]})
    if any(type(claims[k]) is not int for k in ("iat", "exp", "nbf")):
        raise jwt.InvalidTokenError("Invalid timestamps")
    if claims["typ"] != "access" or not 0 < claims["exp"] - claims["iat"] <= 900:
        raise jwt.InvalidTokenError("Invalid access token")
    try:
        return uuid.UUID(claims["sub"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise jwt.InvalidTokenError("Invalid subject") from exc


async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    try:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise jwt.InvalidTokenError()
        user_id = decode_access(credentials.credentials)
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}) from None
    async with Session() as db:
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(401, "Authentication required")
        return user


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def encrypt_tokens(payload: dict, user_id: uuid.UUID) -> str:
    key = base64.b64decode(settings.token_encryption_key.get_secret_value(), validate=True)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), f"synapse:google:{user_id}:v1".encode())
    return "v1." + base64.b64encode(nonce + ciphertext).decode()


def decrypt_tokens(value: str, user_id: uuid.UUID) -> dict:
    version, encoded = value.split(".", 1)
    if version != "v1":
        raise ValueError("Unknown encryption version")
    data = base64.b64decode(encoded, validate=True)
    key = base64.b64decode(settings.token_encryption_key.get_secret_value(), validate=True)
    return json.loads(AESGCM(key).decrypt(data[:12], data[12:], f"synapse:google:{user_id}:v1".encode()))


def set_refresh_cookie(response: Response, raw: str, expires_at):
    response.set_cookie(settings.refresh_cookie, raw, httponly=True, secure=settings.secure_cookies,
                        samesite="strict", path="/", expires=expires_at,
                        max_age=max(0, int((expires_at - utcnow()).total_seconds())))


def clear_refresh_cookie(response: Response):
    response.delete_cookie(settings.refresh_cookie, path="/", secure=settings.secure_cookies,
                           httponly=True, samesite="strict")


def clear_oauth_cookie(response: Response):
    # Cancel pending browser consent on account transitions/disconnect; accepted callbacks may finish.
    response.delete_cookie(settings.oauth_state_cookie, path="/", secure=settings.secure_cookies,
                           httponly=True, samesite="lax")
