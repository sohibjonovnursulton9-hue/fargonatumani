"""Role-checked internal notes and authenticated Telegram attachment access."""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import Response, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from app.change_models import ComplaintInternalNote
from app.config import get_settings
from app.database import get_session
from app.models import AdminRole, Attachment, AuditEvent


def build_complaint_tools_router(admin_dependency, csrf_dependency, ensure_access):
    router = APIRouter()

    @router.post("/admin/complaints/{complaint_id}/internal-notes")
    async def add_internal_note(
        complaint_id: int,
        body: str = Form(...),
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        await ensure_access(db, complaint_id, admin, write=True)
        clean = body.strip()
        if not clean or len(clean) > 5000:
            raise HTTPException(status_code=400, detail="Izoh 1-5000 belgi oralig‘ida bo‘lsin")
        note = ComplaintInternalNote(complaint_id=complaint_id, author_id=admin.id, body=clean)
        db.add(note)
        await db.flush()
        db.add(AuditEvent(
            action="internal_note_added", entity_type="complaint",
            entity_id=str(complaint_id), actor_type="admin", actor_id=str(admin.id),
            new_value=json.dumps({"note_id": note.id}),
        ))
        await db.commit()
        return RedirectResponse(f"/admin/complaints/{complaint_id}#internal-notes", status_code=303)

    @router.get("/admin/attachments/{attachment_id}")
    async def view_attachment(
        attachment_id: int,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
    ):
        attachment = (await db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.is_response_attachment.is_(False),
            )
        )).scalar_one_or_none()
        if attachment is None:
            raise HTTPException(status_code=404)
        await ensure_access(db, attachment.complaint_id, admin, include_deleted=True)
        settings = get_settings()
        if not settings.telegram_bot_token:
            raise HTTPException(status_code=503, detail="Ilova xizmati sozlanmagan")
        if attachment.file_size and attachment.file_size > settings.max_file_size:
            raise HTTPException(status_code=413, detail="Ilova hajmi ruxsat etilgan chegaradan katta")
        try:
            async with Bot(token=settings.telegram_bot_token) as bot:
                remote = await bot.get_file(attachment.telegram_file_id)
                remote_size = getattr(remote, "file_size", None)
                if remote_size and remote_size > settings.max_file_size:
                    raise HTTPException(status_code=413, detail="Ilova hajmi ruxsat etilgan chegaradan katta")
                content = bytes(await remote.download_as_bytearray())
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Telegram ilovasini olish imkoni bo‘lmadi") from exc
        safe_inline_types = {"image/jpeg", "image/png", "image/webp", "application/pdf", "video/mp4"}
        media_type = attachment.mime_type if attachment.mime_type in safe_inline_types else "application/octet-stream"
        disposition = "inline" if media_type in safe_inline_types else "attachment"
        filename = quote((attachment.file_name or f"ilova-{attachment.id}")[:180], safe="")
        db.add(AuditEvent(
            action="complaint_attachment_viewed", entity_type="complaint",
            entity_id=str(attachment.complaint_id), actor_type="admin", actor_id=str(admin.id),
            new_value=json.dumps({"attachment_id": attachment.id}),
        ))
        await db.commit()
        return Response(
            content=content, media_type=media_type,
            headers={
                "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
            },
        )

    return router
