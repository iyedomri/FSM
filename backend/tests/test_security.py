import asyncio
import io
import uuid
from datetime import timedelta

import fakeredis.aioredis
import httpx
import jwt
import pytest
from cryptography.exceptions import InvalidTag
from fastapi import FastAPI, Request
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from ai.pipeline import PipelineError, Question, StudyPack, extract_pdf
from config import settings
from db import utcnow
from schemas import ScheduleInput
from security import limits
from security.auth import decode_access, decrypt_tokens, encrypt_tokens, issue_access
from security.middleware import GuardMiddleware


def test_aes_authentication_and_nonce():
    owner = uuid.uuid4()
    payload = {"refresh_token": "sensitive"}
    first, second = encrypt_tokens(payload, owner), encrypt_tokens(payload, owner)
    assert first != second and "sensitive" not in first
    assert decrypt_tokens(first, owner) == payload
    with pytest.raises(InvalidTag):
        decrypt_tokens(first, uuid.uuid4())
    data = first[:-4] + ("AAAA" if first[-4:] != "AAAA" else "BBBB")
    with pytest.raises(InvalidTag):
        decrypt_tokens(data, owner)


def test_jwt_lifetime_and_verification():
    owner = uuid.uuid4()
    token = issue_access(owner)
    assert decode_access(token) == owner
    claims = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"], audience="synapse-web")
    assert claims["exp"] - claims["iat"] == 900
    claims["exp"] = utcnow() - timedelta(seconds=1)
    expired = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access(expired)
    with pytest.raises(jwt.InvalidTokenError):
        decode_access(jwt.encode(claims, "different-key" * 8, algorithm="HS256"))


async def test_redis_quota_atomic(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(limits, "redis", fake)
    from fastapi import HTTPException
    async def call():
        try:
            await limits.enforce_rate("same-user", 10, 3600)
            return True
        except HTTPException as exc:
            assert exc.status_code == 429
            assert int(exc.headers["Retry-After"]) > 0
            return False
    results = await asyncio.gather(*(call() for _ in range(30)))
    assert sum(results) == 10
    await limits.enforce_rate("other-user", 10, 3600)
    await fake.aclose()


async def test_origin_and_chunked_body_limits():
    app = FastAPI()
    app.add_middleware(GuardMiddleware)
    @app.post("/echo")
    async def echo(request: Request):
        return {"length": len(await request.body())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/echo", content="hi")).status_code == 403
        headers = {"Origin": settings.public_origin}
        assert (await client.post("/echo", headers=headers, content="hi")).json()["length"] == 2
        async def large():
            yield b"x" * 40000
            yield b"x" * 40000
        assert (await client.post("/echo", headers=headers, content=large())).status_code == 413


def test_strict_study_schema():
    question = dict(question="Which statement follows?", choices=["a", "b", "c", "d"], correct_index=0,
                    explanation="The text supports choice a.", source_chunks=[1])
    card = dict(front="What is inertia?", back="Resistance to changes in motion.", source_chunks=[1])
    payload = dict(title="Mechanics", summary_markdown="A source-grounded summary. " * 5,
                   questions=[question] * 5, flashcards=[card] * 3)
    assert len(StudyPack.model_validate(payload).questions) == 5
    with pytest.raises(ValidationError):
        StudyPack.model_validate({**payload, "questions": [question] * 4})
    with pytest.raises(ValidationError):
        Question.model_validate({**question, "choices": ["a"] * 4})
    with pytest.raises(ValidationError):
        Question.model_validate({**question, "correct_index": 4})
    with pytest.raises(ValidationError):
        ScheduleInput(course="Physics", room="A101", weekday=0, start_minute=600, end_minute=500)


async def test_memory_pdf_extraction():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 700 Td (" + b"Newton describes force and acceleration. " * 8 + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages = await extract_pdf(bytearray(buffer.getvalue()))
    assert pages[0]["page"] == 1 and "Newton" in pages[0]["text"]
    with pytest.raises(PipelineError):
        await extract_pdf(bytearray(b"not a PDF"))
