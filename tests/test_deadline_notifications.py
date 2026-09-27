from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.bot_config import DEFAULT_BOT_CONFIG
from app.deadline_notifications import queue_deadline_notices
from app.models import (
    AdminRole, AdminUser, Category, ComplaintLanguage, ComplaintStatus,
    ComplaintType, Deadline, Notification, StatusEvent, User, utcnow,
)
from app.deadline_notifications import queue_confirmation_reminders
from app.services import ComplaintService, DeadlineService


@pytest.mark.asyncio
async def test_deadline_reminder_and_escalation_are_queued_once(db_session):
    user = User(telegram_id=779001, interface_language="uz")
    supervisor = AdminUser(
        username="deadline-supervisor", password_hash="test",
        full_name="Synthetic Supervisor", role=AdminRole.DISTRICT_SUPERVISOR.value,
        telegram_id=779002,
    )
    category = Category(
        code="deadline-test", name_uz="Test", name_uz_cyrillic="Test", name_ru="Test",
    )
    db_session.add_all([user, supervisor, category])
    await db_session.flush()
    complaint = await ComplaintService.create_complaint(
        session=db_session, user_id=user.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id, full_name="Synthetic Citizen",
        phone_number="+998900000002", title="Synthetic deadline",
        complaint_text="Synthetic test only",
    )
    deadline = (await db_session.execute(
        select(Deadline).where(Deadline.complaint_id == complaint.id)
    )).scalar_one()
    now = utcnow()
    deadline.current_deadline = now + timedelta(days=1)
    config = deepcopy(DEFAULT_BOT_CONFIG)

    queued = await queue_deadline_notices(db_session, config, now=now)
    assert queued == {"reminders": 1, "escalations": 0}
    await db_session.commit()

    again = await queue_deadline_notices(db_session, config, now=now)
    assert again["reminders"] == 0
    deadline.current_deadline = now - timedelta(days=4)
    escalated = await queue_deadline_notices(db_session, config, now=now)
    assert escalated["escalations"] == 1
    await db_session.commit()

    duplicate = await queue_deadline_notices(db_session, config, now=now)
    assert duplicate["escalations"] == 0
    notifications = (await db_session.execute(
        select(Notification).where(Notification.complaint_id == complaint.id)
    )).scalars().all()
    assert [item.notification_type for item in notifications].count("deadline_reminder") == 1
    assert [item.notification_type for item in notifications].count("deadline_escalation") == 1


@pytest.mark.asyncio
async def test_confirmation_reminder_waits_configured_time_and_is_idempotent(db_session):
    category = Category(
        code="confirmation-reminder-test", name_uz="Test",
        name_uz_cyrillic="Тест", name_ru="Тест",
    )
    aged_user = User(telegram_id=779101, interface_language="ru")
    recent_user = User(telegram_id=779102, interface_language="uz")
    db_session.add_all([category, aged_user, recent_user])
    await db_session.flush()
    aged = await ComplaintService.create_complaint(
        session=db_session, user_id=aged_user.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id, full_name="Synthetic Aged Citizen",
        phone_number="+998900000101", title="Synthetic pending confirmation",
        complaint_text="Synthetic test only",
    )
    recent = await ComplaintService.create_complaint(
        session=db_session, user_id=recent_user.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id, full_name="Synthetic Recent Citizen",
        phone_number="+998900000102", title="Synthetic recent confirmation",
        complaint_text="Synthetic test only",
    )
    now = utcnow()
    pending = ComplaintStatus.CITIZEN_CONFIRMATION_PENDING.value
    aged.status = pending
    recent.status = pending
    db_session.add_all([
        StatusEvent(
            complaint_id=aged.id, from_status=ComplaintStatus.IMPLEMENTATION_REPORTED.value,
            to_status=pending, actor_type="admin", actor_id="1",
            created_at=now - timedelta(hours=49),
        ),
        StatusEvent(
            complaint_id=recent.id, from_status=ComplaintStatus.IMPLEMENTATION_REPORTED.value,
            to_status=pending, actor_type="admin", actor_id="1",
            created_at=now - timedelta(hours=47),
        ),
    ])
    config = deepcopy(DEFAULT_BOT_CONFIG)
    config["confirmation_reminder_message_ru"] = "Ответ по делу {tracking_id}."

    assert await queue_confirmation_reminders(db_session, config, now=now) == 1
    await db_session.commit()
    assert await queue_confirmation_reminders(db_session, config, now=now) == 0
    notifications = (await db_session.execute(
        select(Notification).where(
            Notification.notification_type == "citizen_confirmation_reminder"
        )
    )).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].complaint_id == aged.id
    assert notifications[0].language == "ru"
    assert aged.tracking_id in notifications[0].message_text


@pytest.mark.asyncio
async def test_notification_delivery_uses_exponential_retry_schedule(db_session, monkeypatch):
    from app import bot as bot_module

    notification = Notification(
        recipient_telegram_id=779201,
        notification_type="test",
        message_text="Synthetic notification",
        language="uz",
    )
    db_session.add(notification)
    await db_session.commit()

    @asynccontextmanager
    async def session_context():
        yield db_session

    monkeypatch.setattr(
        bot_module, "get_session_factory", lambda: lambda: session_context()
    )
    send_message = AsyncMock(
        side_effect=[RuntimeError("synthetic failure"), RuntimeError("synthetic failure"),
                     SimpleNamespace(message_id=1)]
    )
    fake_bot = SimpleNamespace(send_message=send_message)
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)

    await bot_module.deliver_pending_notifications(fake_bot, now=now)
    assert notification.retry_count == 1
    assert notification.next_retry_at == now + timedelta(seconds=30)

    await bot_module.deliver_pending_notifications(fake_bot, now=now + timedelta(seconds=29))
    assert send_message.await_count == 1

    await bot_module.deliver_pending_notifications(fake_bot, now=now + timedelta(seconds=30))
    assert notification.retry_count == 2
    assert notification.next_retry_at == now + timedelta(seconds=90)

    await bot_module.deliver_pending_notifications(fake_bot, now=now + timedelta(seconds=89))
    assert send_message.await_count == 2

    await bot_module.deliver_pending_notifications(fake_bot, now=now + timedelta(seconds=90))
    assert send_message.await_count == 3
    assert notification.sent is True
    assert notification.next_retry_at is None
    assert notification.error_message is None


@pytest.mark.asyncio
async def test_deadline_extension_is_validated_and_notified_after_delivery(db_session, monkeypatch):
    from app import bot as bot_module

    user = User(telegram_id=779301, interface_language="uz")
    admin = AdminUser(
        username="deadline-extension-approver", password_hash="test",
        full_name="Synthetic Approver", role=AdminRole.SUPER_ADMIN.value,
    )
    category = Category(
        code="extension-test", name_uz="Test", name_uz_cyrillic="Тест", name_ru="Test",
    )
    db_session.add_all([user, admin, category])
    await db_session.flush()
    complaint = await ComplaintService.create_complaint(
        session=db_session, user_id=user.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id, full_name="Synthetic Extension Citizen",
        phone_number="+998900000301", title="Synthetic extension case",
        complaint_text="Synthetic test only",
    )
    deadline = (await db_session.execute(
        select(Deadline).where(Deadline.complaint_id == complaint.id)
    )).scalar_one()
    current = deadline.current_deadline

    for invalid_reason in ("  ", "x" * 1001):
        with pytest.raises(ValueError):
            await DeadlineService.extend_deadline(
                db_session, deadline.id, current + timedelta(days=1), invalid_reason, admin.id
            )
    with pytest.raises(ValueError):
        await DeadlineService.extend_deadline(
            db_session, deadline.id, current, "No extension", admin.id
        )
    with pytest.raises(ValueError):
        await DeadlineService.extend_deadline(
            db_session, deadline.id, current + timedelta(days=366), "Too long", admin.id
        )

    now = utcnow()
    extension = await DeadlineService.extend_deadline(
        db_session, deadline.id, current + timedelta(days=14), "   Synthetic reason   ", admin.id
    )
    assert extension.reason == "Synthetic reason"
    assert extension.citizen_notified is False
    notification = (await db_session.execute(
        select(Notification).where(Notification.deadline_extension_id == extension.id)
    )).scalar_one()
    assert notification.notification_type == "deadline_extended"
    assert complaint.tracking_id in notification.message_text

    @asynccontextmanager
    async def session_context():
        yield db_session

    monkeypatch.setattr(
        bot_module, "get_session_factory", lambda: lambda: session_context()
    )
    fake_bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=23))
    )
    await bot_module.deliver_pending_notifications(fake_bot, now=now)

    assert notification.sent is True
    assert extension.citizen_notified is True
    assert extension.citizen_notified_at == now
