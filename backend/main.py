import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import unquote, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Date, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ai.pipeline import PipelineError, ask_document, delete_vectors
from auth_routes import router as auth_router
from classroom.routes import router as classroom_router
from config import settings
from db import Document, ScheduleItem, StudySession, User, engine, get_db, utcnow
from schemas import Ask, Attempt, ScheduleInput
from security.auth import current_user
from security.limits import enforce_rate, redis
from security.middleware import GuardMiddleware
from services import ingest_document


@asynccontextmanager
async def lifespan(app):
    await redis.ping()
    async with engine.connect() as connection:
        await connection.execute(select(1))
    yield
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="Project Synapse", version="1.0.0", lifespan=lifespan,
              docs_url="/docs" if settings.environment == "development" else None,
              redoc_url=None, openapi_url="/openapi.json" if settings.environment == "development" else None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list({urlsplit(settings.public_origin).hostname,
                                                            "backend", "localhost", "127.0.0.1"}))
app.add_middleware(GuardMiddleware)
app.include_router(auth_router)
app.include_router(classroom_router)


@app.exception_handler(PipelineError)
async def pipeline_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


def document_view(doc, full=False):
    result = {"id": str(doc.id), "filename": doc.filename, "source": doc.source, "status": doc.status,
              "title": doc.payload["title"] if doc.payload else doc.filename, "best_score": doc.best_score,
              "completed": bool(doc.completed_at), "created_at": doc.created_at.isoformat()}
    if full:
        result.update({"pack": doc.payload, "pages": doc.pages or []})
    return result


async def owned_document(db, user_id, document_id, lock=False):
    query = select(Document).where(Document.id == document_id, Document.user_id == user_id)
    doc = await db.scalar(query.with_for_update() if lock else query)
    if not doc:
        raise HTTPException(404, "Lesson not found")
    return doc


@app.get("/api/health/live")
async def live():
    return {"status": "alive"}


@app.get("/api/health/ready")
async def ready(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(1))
        await redis.ping()
    except Exception:
        return JSONResponse({"status": "unavailable"}, 503)
    return {"status": "ready"}


@app.post("/api/ai/generate", status_code=201)
async def generate(request: Request, user=Depends(current_user)):
    # No UploadFile/FormData: Starlette's default multipart spool writes large files to disk.
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/pdf":
        raise HTTPException(415, "Send raw application/pdf bytes, not multipart form data")
    await enforce_rate(f"uploads:{user.id}", 20, 3600)
    data = bytearray()
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(data) + len(chunk) > settings.max_pdf_bytes:
                    raise HTTPException(413, "PDF exceeds 10 MiB")
                data.extend(chunk)
        if not data.startswith(b"%PDF-"):
            raise HTTPException(415, "The uploaded content is not a PDF")
        filename = unquote(request.headers.get("x-file-name", "chapter.pdf")).replace("\\", "/").split("/")[-1]
        filename = "".join(c for c in filename if c.isprintable())[:160] or "chapter.pdf"
        doc = await ingest_document(user.id, data, filename)
        return document_view(doc, True)
    except TimeoutError:
        raise HTTPException(408, "Upload timed out") from None
    finally:
        data[:] = b"\x00" * len(data)
        data.clear()


@app.get("/api/documents")
async def documents(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    docs = (await db.scalars(select(Document).where(Document.user_id == user.id)
                            .order_by(Document.created_at.desc()).limit(100))).all()
    return [document_view(doc) for doc in docs]


@app.get("/api/documents/{document_id}")
async def document(document_id: UUID, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    return document_view(await owned_document(db, user.id, document_id), True)


@app.delete("/api/documents/{document_id}", status_code=204)
async def delete_document(document_id: UUID, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    doc = await owned_document(db, user.id, document_id)
    if doc.status == "processing":
        raise HTTPException(409, "Wait for processing to finish before deleting")
    await enforce_rate(f"delete:{user.id}", 30, 3600)
    await db.commit()  # Do not hold a database connection during the provider request.
    try:
        await delete_vectors(user.id, document_id)
    except Exception:
        raise HTTPException(503, "Vector deletion failed; lesson retained for a safe retry") from None
    await db.execute(delete(Document).where(Document.id == document_id, Document.user_id == user.id))
    await db.commit()


@app.post("/api/documents/{document_id}/attempts")
async def attempt(document_id: UUID, body: Attempt, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    await enforce_rate(f"quiz:{user.id}", 60, 3600)
    doc = await owned_document(db, user.id, document_id, lock=True)
    if doc.status != "ready":
        raise HTTPException(409, "Lesson is not ready")
    score = sum(answer == question["correct_index"] for answer, question in zip(body.answers, doc.payload["questions"], strict=True))
    first_pass = score >= 3 and doc.completed_at is None
    doc.best_score = max(doc.best_score, score)
    if first_pass:
        doc.completed_at = utcnow()
    await db.commit()
    return {"score": score, "out_of": 5, "xp_awarded": 50 if first_pass else 0, "passed": score >= 3}


@app.post("/api/documents/{document_id}/ask")
async def ask(document_id: UUID, body: Ask, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    doc = await owned_document(db, user.id, document_id)
    if doc.status != "ready":
        raise HTTPException(409, "Lesson is not ready")
    await db.commit()
    await enforce_rate(f"ai:{user.id}", 10, 3600)
    try:
        async with asyncio.timeout(150):
            return await ask_document(user.id, document_id, body.question)
    except PipelineError:
        raise
    except Exception:
        raise HTTPException(503, "AI provider unavailable; retry later") from None


@app.get("/api/schedule")
async def schedule(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    items = (await db.scalars(select(ScheduleItem).where(ScheduleItem.user_id == user.id)
                             .order_by(ScheduleItem.weekday, ScheduleItem.start_minute))).all()
    return [{"id": str(i.id), "course": i.course, "room": i.room, "weekday": i.weekday,
             "start_minute": i.start_minute, "end_minute": i.end_minute} for i in items]


@app.post("/api/schedule", status_code=201)
async def add_schedule(body: ScheduleInput, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    # Serialize changes for one student so overlap checks cannot race.
    await db.scalar(select(User.id).where(User.id == user.id).with_for_update())
    conflict = await db.scalar(select(ScheduleItem.id).where(ScheduleItem.user_id == user.id,
                               ScheduleItem.weekday == body.weekday, ScheduleItem.start_minute < body.end_minute,
                               ScheduleItem.end_minute > body.start_minute))
    if conflict:
        raise HTTPException(409, "This slot overlaps an existing class")
    item = ScheduleItem(user_id=user.id, **body.model_dump())
    db.add(item)
    await db.commit()
    return {"id": str(item.id)}


@app.delete("/api/schedule/{slot_id}", status_code=204)
async def remove_schedule(slot_id: UUID, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(delete(ScheduleItem).where(ScheduleItem.id == slot_id, ScheduleItem.user_id == user.id))
    if not result.rowcount:
        raise HTTPException(404, "Class not found")
    await db.commit()


@app.post("/api/study/start")
async def start_study(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.scalar(select(User.id).where(User.id == user.id).with_for_update())
    item = await db.scalar(select(StudySession).where(StudySession.user_id == user.id, StudySession.ended_at.is_(None)))
    if item is None:
        item = StudySession(user_id=user.id)
        db.add(item)
        await db.commit()
    return {"id": str(item.id), "started_at": item.started_at.isoformat()}


@app.post("/api/study/{session_id}/stop")
async def stop_study(session_id: UUID, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    item = await db.scalar(select(StudySession).where(StudySession.id == session_id,
                           StudySession.user_id == user.id).with_for_update())
    if not item:
        raise HTTPException(404, "Focus session not found")
    if item.ended_at is None:
        item.ended_at = utcnow()
        item.seconds = max(0, min(7200, int((item.ended_at - item.started_at).total_seconds())))
        await db.commit()
    return {"seconds": item.seconds}


@app.get("/api/dashboard")
async def dashboard(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    today = utcnow().astimezone(ZoneInfo(settings.timezone)).date()
    local_day = cast(func.timezone(settings.timezone, StudySession.ended_at), Date)
    daily = (await db.execute(select(local_day, func.sum(StudySession.seconds)).where(
        StudySession.user_id == user.id, StudySession.ended_at >= utcnow() - timedelta(days=366))
        .group_by(local_day))).all()
    minutes = {day: int(seconds) // 60 for day, seconds in daily}
    completed = (await db.scalars(select(Document.completed_at).where(Document.user_id == user.id,
                                                                   Document.completed_at.is_not(None)))).all()
    completions = {}
    for timestamp in completed:
        day = timestamp.astimezone(ZoneInfo(settings.timezone)).date()
        completions[day] = completions.get(day, 0) + 1
    active_days = {day for day, value in minutes.items() if value >= 1} | set(completions)
    cursor, streak = today if today in active_days else today - timedelta(days=1), 0
    while cursor in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    total_seconds = await db.scalar(select(func.coalesce(func.sum(StudySession.seconds), 0)).where(StudySession.user_id == user.id))
    active = await db.scalar(select(StudySession).where(StudySession.user_id == user.id, StudySession.ended_at.is_(None)))
    return {"timezone": settings.timezone, "today_minutes": minutes.get(today, 0), "streak": streak,
            "completed_lessons": len(completed), "xp": len(completed) * 50 + int(total_seconds) // 60,
            "week": [{"date": (day := today - timedelta(days=offset)).isoformat(),
                      "minutes": minutes.get(day, 0), "lessons": completions.get(day, 0)} for offset in range(6, -1, -1)],
            "active_session": {"id": str(active.id), "started_at": active.started_at.isoformat()} if active else None}
