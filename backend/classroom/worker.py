"""Leased PostgreSQL polling jobs. No PDFs or OAuth credentials are queued in Redis."""
import asyncio
import hashlib
import logging
from datetime import timedelta
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select, update

from ai.pipeline import PipelineError, delete_vectors
from config import settings
from db import Document, GoogleConnection, RefreshToken, Session, engine, utcnow
from security.auth import decrypt_tokens, encrypt_tokens
from security.limits import redis
from services import ingest_document

logger = logging.getLogger("synapse.worker")


async def get_pages(client, url, field, headers, params=None):
    params = dict(params or {})
    for _ in range(10):
        response = await client.get(url, headers=headers, params={**params, "pageSize": 100})
        response.raise_for_status()
        payload = response.json()
        for item in payload.get(field, []):
            yield item
        if not payload.get("nextPageToken"):
            return
        params["pageToken"] = payload["nextPageToken"]


async def sync_user(user_id):
    async with Session() as db:
        connection = await db.get(GoogleConnection, user_id)
        if not connection:
            return
        credential_snapshot = connection.credentials
        tokens = decrypt_tokens(credential_snapshot, user_id)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        if tokens.get("expires_at", 0) < utcnow().timestamp() + 60:
            response = await client.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                "client_id": settings.google_client_id, "client_secret": settings.google_client_secret.get_secret_value()})
            response.raise_for_status()
            tokens.update(response.json())
            tokens["expires_at"] = utcnow().timestamp() + int(tokens.get("expires_in", 3600))
            refreshed = encrypt_tokens(tokens, user_id)
            async with Session() as db:
                result = await db.execute(update(GoogleConnection).where(
                    GoogleConnection.user_id == user_id,
                    GoogleConnection.credentials == credential_snapshot,
                ).values(credentials=refreshed))
                await db.commit()
                if not result.rowcount:
                    return  # Disconnect/reconnect won the race; never overwrite a new grant.
            credential_snapshot = refreshed
        headers, imported = {"Authorization": "Bearer " + tokens["access_token"]}, 0
        async for course in get_pages(client, "https://classroom.googleapis.com/v1/courses", "courses", headers,
                                      {"courseStates": "ACTIVE"}):
            course_id = quote(str(course["id"]), safe="")
            url = f"https://classroom.googleapis.com/v1/courses/{course_id}/courseWorkMaterials"
            async for material in get_pages(client, url, "courseWorkMaterial", headers):
                for attachment in material.get("materials", []):
                    file = attachment.get("driveFile", {}).get("driveFile", {})
                    if not file.get("id"):
                        continue
                    source_key = hashlib.sha256(f"{course['id']}:{material['id']}:{file['id']}:{material.get('updateTime', '')}".encode()).hexdigest()
                    async with Session() as db:
                        link = await db.get(GoogleConnection, user_id)
                        if not link or link.credentials != credential_snapshot:
                            return  # Consent was removed or replaced during this job.
                        existing = await db.scalar(select(Document).where(Document.user_id == user_id, Document.source_key == source_key))
                        if existing and existing.status in {"ready", "processing"}:
                            continue
                    base = "https://www.googleapis.com/drive/v3/files/" + quote(str(file["id"]), safe="")
                    metadata = await client.get(base, headers=headers, params={"fields": "mimeType,size,name"})
                    metadata.raise_for_status()
                    meta = metadata.json()
                    if meta.get("mimeType") != "application/pdf" or int(meta.get("size", 0)) > settings.max_pdf_bytes:
                        continue
                    data = bytearray()
                    try:
                        async with client.stream("GET", base, headers=headers, params={"alt": "media"}) as download:
                            download.raise_for_status()
                            async for chunk in download.aiter_bytes():
                                if len(data) + len(chunk) > settings.max_pdf_bytes:
                                    raise PipelineError("Classroom PDF exceeds the size limit")
                                data.extend(chunk)
                        await ingest_document(user_id, data, meta.get("name", "classroom.pdf"), "classroom", source_key)
                        imported += 1
                    finally:
                        data[:] = b"\x00" * len(data)
                        data.clear()
                    if imported >= 3:
                        return  # Bounded work; next poll resumes from unprocessed material.


async def cleanup():
    async with Session() as db:
        await db.execute(delete(RefreshToken).where(RefreshToken.expires_at < utcnow()))
        stale = (await db.scalars(select(Document).where(or_(
            and_(Document.status == "processing", Document.created_at < utcnow() - timedelta(minutes=20)),
            Document.status == "cleanup_pending",
        )).order_by(Document.created_at, Document.id).limit(20))).all()
        await db.commit()
    for document in stale:
        try:
            await delete_vectors(document.user_id, document.id)
            async with Session() as db:
                row = await db.get(Document, document.id)
                if row:
                    row.status = "failed"
                    await db.commit()
        except Exception:
            logger.error("Deferred vector cleanup for %s", document.id)


async def run():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    last_cleanup = 0.0
    try:
        while True:
            try:
                if asyncio.get_running_loop().time() - last_cleanup > 3600:
                    await cleanup()
                    last_cleanup = asyncio.get_running_loop().time()
                async with Session() as db:
                    connection = await db.scalar(select(GoogleConnection).where(
                        GoogleConnection.next_sync_at <= utcnow(),
                        or_(GoogleConnection.lease_until.is_(None), GoogleConnection.lease_until < utcnow()))
                        .with_for_update(skip_locked=True).limit(1))
                    user_id = connection.user_id if connection else None
                    if connection:
                        connection.lease_until = utcnow() + timedelta(minutes=15)
                        await db.commit()
                if user_id:
                    error = None
                    try:
                        async with asyncio.timeout(780):
                            await sync_user(user_id)
                    except HTTPException as exc:
                        error = "Hourly AI quota reached; sync will retry later." if exc.status_code == 429 else "Processing busy; retrying later."
                    except Exception as exc:
                        logger.error("Sync failed: %s", type(exc).__name__)
                        error = "Sync failed. Check credentials, PDF limits, or reconnect Google."
                    async with Session() as db:
                        connection = await db.get(GoogleConnection, user_id)
                        if connection:
                            connection.last_sync_at, connection.last_error = utcnow(), error
                            connection.next_sync_at = utcnow() + timedelta(minutes=5)
                            connection.lease_until = None
                            await db.commit()
                else:
                    await asyncio.sleep(15)
            except Exception as exc:
                logger.error("Worker unavailable: %s", type(exc).__name__)
                await asyncio.sleep(15)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
