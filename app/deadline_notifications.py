"""Queue deduplicated citizen reminders and supervisor escalations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AdminRole,
    AdminUser,
    AuditEvent,
    Complaint,
    ComplaintStatus,
    Deadline,
    Notification,
    StatusEvent,
    TERMINAL_STATUSES,
    User,
)


def _render(template: str, tracking_id: str, deadline: datetime) -> str:
    return (
        template.replace("{tracking_id}", tracking_id)
        .replace("{deadline}", deadline.strftime("%d.%m.%Y %H:%M"))
    )[:4000]


def _language(value: str | None) -> str:
    if value == "uz_cyrillic":
        return "uz_cyrillic"
    if value in {"ru"}:
        return "ru"
    return "uz"


def _message(config: dict[str, Any], prefix: str, language: str, tracking_id: str, due: datetime) -> str:
    locale = _language(language)
    template = config.get(f"{prefix}_{locale}") or config.get(f"{prefix}_uz") or ""
    return _render(template, tracking_id, due)


async def queue_deadline_notices(
    session: AsyncSession,
    config: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, int]:
    """Queue one pre-deadline reminder and one overdue escalation per case."""
    now = now or datetime.now(timezone.utc)
    policy = config.get("deadline_policy", {})
    reminder_days = max(1, min(30, int(policy.get("reminder_days_before", 3))))
    escalation_days = max(1, min(365, int(policy.get("escalate_after_days", 3))))
    terminal = [status.value for status in TERMINAL_STATUSES]
    reminders = escalations = 0

    reminder_rows = await session.execute(
        select(Deadline, Complaint, User)
        .join(Complaint, Complaint.id == Deadline.complaint_id)
        .join(User, User.id == Complaint.user_id)
        .where(
            Deadline.current_deadline > now,
            Deadline.current_deadline <= now + timedelta(days=reminder_days),
            Deadline.warning_sent.is_(False),
            Complaint.archived_at.is_(None),
            Complaint.deleted_at.is_(None),
            Complaint.status.not_in(terminal),
        )
    )
    for deadline, complaint, user in reminder_rows.all():
        if not user.telegram_id:
            continue
        language = _language(complaint.complaint_language)
        session.add(Notification(
            complaint_id=complaint.id,
            recipient_telegram_id=user.telegram_id,
            notification_type="deadline_reminder",
            message_text=_message(
                config, "deadline_reminder_message", language,
                complaint.tracking_id, deadline.current_deadline,
            ),
            language=language,
        ))
        deadline.warning_sent = True
        reminders += 1

    overdue_rows = await session.execute(
        select(Deadline, Complaint)
        .join(Complaint, Complaint.id == Deadline.complaint_id)
        .where(
            Deadline.current_deadline <= now - timedelta(days=escalation_days),
            Deadline.exceeded_notified.is_(False),
            Complaint.archived_at.is_(None),
            Complaint.deleted_at.is_(None),
            Complaint.status.not_in(terminal),
        )
    )
    supervisors = (await session.execute(
        select(AdminUser).where(
            AdminUser.is_active.is_(True),
            AdminUser.telegram_id.is_not(None),
            AdminUser.role.in_([
                AdminRole.SUPER_ADMIN.value,
                AdminRole.DISTRICT_SUPERVISOR.value,
            ]),
        )
    )).scalars().all()

    if supervisors:
        for deadline, complaint in overdue_rows.all():
            for supervisor in supervisors:
                language = "uz"
                session.add(Notification(
                    complaint_id=complaint.id,
                    recipient_telegram_id=supervisor.telegram_id,
                    notification_type="deadline_escalation",
                    message_text=_message(
                        config, "escalation_message", language,
                        complaint.tracking_id, deadline.current_deadline,
                    ),
                    language=language,
                ))
                escalations += 1
            deadline.exceeded_notified = True
            session.add(AuditEvent(
                action="complaint_escalation_queued",
                entity_type="complaint",
                entity_id=str(complaint.id),
                actor_type="system",
                new_value='{"escalation":"deadline_overdue"}',
            ))

    await session.flush()
    return {"reminders": reminders, "escalations": escalations}


async def queue_confirmation_reminders(
    session: AsyncSession,
    config: dict[str, Any],
    now: datetime | None = None,
) -> int:
    """Queue one reminder after the configured wait for each pending-confirmation cycle."""
    now = now or datetime.now(timezone.utc)
    policy = config.get("deadline_policy", {})
    hours = max(1, min(336, int(policy.get("confirmation_reminder_hours", 48))))
    remind_before = now - timedelta(hours=hours)
    pending = ComplaintStatus.CITIZEN_CONFIRMATION_PENDING.value
    pending_since = (
        select(func.max(StatusEvent.created_at))
        .where(
            StatusEvent.complaint_id == Complaint.id,
            StatusEvent.to_status == pending,
        )
        .correlate(Complaint)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Complaint, User, pending_since.label("pending_since"))
        .join(User, User.id == Complaint.user_id)
        .where(
            Complaint.status == pending,
            Complaint.archived_at.is_(None),
            Complaint.deleted_at.is_(None),
            pending_since <= remind_before,
        )
    )
    queued = 0
    for complaint, user, entered_at in rows.all():
        if not user.telegram_id or entered_at is None:
            continue
        already_queued = await session.execute(
            select(Notification.id)
            .where(
                Notification.complaint_id == complaint.id,
                Notification.notification_type == "citizen_confirmation_reminder",
                Notification.created_at >= entered_at,
            )
            .limit(1)
        )
        if already_queued.scalar_one_or_none() is not None:
            continue
        language = _language(user.interface_language)
        template = (
            config.get(f"confirmation_reminder_message_{language}")
            or config.get("confirmation_reminder_message_uz")
            or "Murojaat {tracking_id} bo‘yicha javob berildi."
        )
        message = template.replace("{tracking_id}", complaint.tracking_id)[:4000]
        session.add(Notification(
            complaint_id=complaint.id,
            recipient_telegram_id=user.telegram_id,
            notification_type="citizen_confirmation_reminder",
            message_text=message,
            language=language,
        ))
        queued += 1
    await session.flush()
    return queued
