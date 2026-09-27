"""Admin-only inspection and controlled retry for the Telegram outbox."""
from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.admin.csrf import get_or_create_csrf_token
from app.models import AuditAction, AuditEvent, Notification, utcnow


MAX_TOTAL_ATTEMPTS = 10
PAGE_SIZE = 50
RETRY_REASONS = {
    "telegram_restored": "Telegram aloqasi tiklandi",
    "recipient_verified": "Qabul qiluvchi qayta tekshirildi",
    "other_reviewed": "Boshqa sabab ko‘rib chiqildi",
}


def _status_conditions(now: datetime) -> dict[str, object]:
    due = or_(Notification.next_retry_at.is_(None), Notification.next_retry_at <= now)
    automatic = Notification.retry_count < Notification.max_retries
    return {
        "open": Notification.sent.is_(False),
        "pending": and_(Notification.sent.is_(False), automatic, due),
        "retrying": and_(
            Notification.sent.is_(False),
            automatic,
            Notification.next_retry_at > now,
        ),
        "failed": and_(
            Notification.sent.is_(False),
            Notification.retry_count >= Notification.max_retries,
        ),
        "sent": Notification.sent.is_(True),
    }


def _mask_telegram_id(value: int) -> str:
    suffix = str(abs(value))[-4:]
    return f"••••{suffix}"


def _safe_error(value: str | None) -> str | None:
    if not value:
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", value):
        return value
    return "Tafsilot maxfiy saqlandi"


def build_notification_router(
    templates,
    admin_dependency,
    csrf_dependency,
    set_csrf_cookie,
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/notifications", response_class=HTMLResponse)
    async def notification_outbox_page(
        request: Request,
        status: str = "open",
        page: int = 1,
        retried: int | None = None,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
    ):
        now = utcnow()
        conditions = _status_conditions(now)
        status = status if status in {*conditions, "all"} else "open"
        counts = {
            key: int(await db.scalar(select(func.count(Notification.id)).where(condition)) or 0)
            for key, condition in conditions.items()
        }

        stmt = select(Notification)
        if status != "all":
            stmt = stmt.where(conditions[status])
        total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(1, page), total_pages)
        status_expr = case(
            (Notification.sent.is_(True), "sent"),
            (Notification.retry_count >= Notification.max_retries, "failed"),
            (Notification.next_retry_at > now, "retrying"),
            else_="pending",
        )
        rows = (await db.execute(
            select(Notification, status_expr.label("queue_status"))
            .where(*([conditions[status]] if status != "all" else []))
            .order_by(desc(Notification.created_at), desc(Notification.id))
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )).all()
        items = [
            {
                "notification": notification,
                "status": queue_status,
                "masked_recipient": _mask_telegram_id(notification.recipient_telegram_id),
                "safe_error": _safe_error(notification.error_message),
            }
            for notification, queue_status in rows
        ]

        csrf_token = get_or_create_csrf_token(request)
        response = templates.TemplateResponse(
            request=request,
            name="admin/notifications.html",
            context={
                "request": request,
                "admin": admin,
                "csrf_token": csrf_token,
                "items": items,
                "counts": counts,
                "status": status,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "retried": retried,
                "retry_reasons": RETRY_REASONS,
            },
        )
        response.headers["Cache-Control"] = "private, no-store"
        set_csrf_cookie(response, csrf_token)
        return response

    @router.post("/admin/notifications/{notification_id}/retry")
    async def retry_notification(
        notification_id: int,
        request: Request,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        form = await request.form()
        reason = str(form.get("reason", ""))
        if reason not in RETRY_REASONS:
            raise HTTPException(status_code=400, detail="Qayta yuborish sababini tanlang")

        notification = (await db.execute(
            select(Notification)
            .where(Notification.id == notification_id)
            .with_for_update()
        )).scalar_one_or_none()
        if notification is None:
            raise HTTPException(status_code=404, detail="Xabarnoma topilmadi")
        if notification.sent:
            raise HTTPException(status_code=409, detail="Yuborilgan xabarnomani qayta yuborib bo‘lmaydi")
        if notification.retry_count < notification.max_retries:
            raise HTTPException(
                status_code=409,
                detail="Avtomatik qayta urinish davom etmoqda; faqat yakuniy xatoni qo‘lda qaytaring",
            )
        if notification.max_retries >= MAX_TOTAL_ATTEMPTS:
            raise HTTPException(status_code=409, detail="Urinishlar xavfsiz chegarasiga yetgan")

        now = utcnow()
        old_limit = notification.max_retries
        notification.max_retries = old_limit + 1
        notification.next_retry_at = now
        db.add(AuditEvent(
            action=AuditAction.NOTIFICATION_RETRIED.value,
            entity_type="notification",
            entity_id=str(notification.id),
            actor_type="admin",
            actor_id=str(admin.id),
            old_value=json.dumps({"retry_count": notification.retry_count, "max_retries": old_limit}),
            new_value=json.dumps({
                "retry_count": notification.retry_count,
                "max_retries": notification.max_retries,
                "reason": reason,
            }),
            metadata_json=json.dumps({"reason_label": RETRY_REASONS[reason]}),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500] or None,
        ))
        await db.commit()
        return RedirectResponse(
            url=f"/admin/notifications?status=failed&retried={notification.id}",
            status_code=303,
        )

    return router
