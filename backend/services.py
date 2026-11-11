import asyncio
import logging
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from ai.pipeline import PipelineError, delete_vectors, process_pdf, require_ai
from db import Document, Session
from security.limits import enforce_rate, generation_lock

logger = logging.getLogger("synapse")


async def ingest_document(user_id, data: bytearray, filename, source="upload", source_key=None):
    # Own the buffer lifecycle even when configuration, quota or locking fails.
    try:
        return await _ingest_document(user_id, data, filename, source, source_key)
    finally:
        data[:] = b"\x00" * len(data)
        data.clear()


async def _ingest_document(user_id, data: bytearray, filename, source, source_key):
    require_ai()
    document_id = uuid.uuid4()
    async with generation_lock(user_id):
        if source_key:
            async with Session() as db:
                existing = await db.scalar(select(Document).where(Document.user_id == user_id, Document.source_key == source_key))
                if existing and existing.status in {"ready", "processing"}:
                    return existing
                if existing:
                    await delete_vectors(user_id, existing.id)
                    await db.delete(existing)
                    await db.commit()
        await enforce_rate(f"ai:{user_id}", 10, 3600)
        async with Session() as db:
            document = Document(id=document_id, user_id=user_id, filename=filename[:160], source=source, source_key=source_key)
            db.add(document)
            await db.commit()
        try:
            async with asyncio.timeout(240):
                payload, pages = await process_pdf(data, user_id, document_id)
            async with Session() as db:
                document = await db.get(Document, document_id)
                document.payload, document.pages, document.chunk_count = payload, pages, len(pages)
                document.status = "ready"
                await db.commit()
                return document
        except BaseException as exc:
            async def cleanup():
                pending = False
                try:
                    await delete_vectors(user_id, document_id)
                except Exception:
                    pending = True
                    logger.error("Vector cleanup pending for document %s", document_id)
                async with Session() as db:
                    failed = await db.get(Document, document_id)
                    if failed:
                        failed.status = "cleanup_pending" if pending else "failed"
                        await db.commit()
            await asyncio.shield(cleanup())
            if isinstance(exc, (PipelineError, HTTPException, asyncio.CancelledError)):
                raise
            logger.error("Generation failed: %s", type(exc).__name__)
            raise PipelineError("AI provider unavailable or returned invalid output; try again later") from None
        finally:
            data[:] = b"\x00" * len(data)
            data.clear()
