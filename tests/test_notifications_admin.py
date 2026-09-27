import re
from datetime import timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.admin.app import app
from app.database import get_session
from app.models import AdminRole, AdminSession, AdminUser, AuditEvent, Notification, utcnow


async def _authorize_client(db_session, admin):
    token = f"synthetic-session-{admin.username}"
    db_session.add(AdminSession(
        session_token=token,
        admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=1),
        is_active=True,
    ))
    await db_session.commit()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    client.cookies.set("admin_session", token)
    return client


@pytest.mark.asyncio
async def test_superadmin_sees_private_safe_outbox_and_can_retry_final_failure(db_session):
    admin = AdminUser(
        username="notification-admin",
        password_hash="unused",
        full_name="Notification Admin",
        role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.flush()
    failed = Notification(
        recipient_telegram_id=123456789,
        notification_type="status_update",
        message_text="synthetic-private-message-body",
        language="uz",
        retry_count=3,
        max_retries=3,
        error_message="Failure with recipient 123456789: synthetic-private-detail",
    )
    waiting = Notification(
        recipient_telegram_id=987654321,
        notification_type="response",
        message_text="synthetic-waiting-body",
        language="ru",
        retry_count=1,
        max_retries=3,
        next_retry_at=utcnow() + timedelta(hours=1),
    )
    sent = Notification(
        recipient_telegram_id=112233445,
        notification_type="reminder",
        message_text="synthetic-sent-body",
        language="uz",
        sent=True,
        sent_at=utcnow(),
    )
    db_session.add_all([failed, waiting, sent])
    await db_session.commit()

    client = await _authorize_client(db_session, admin)
    try:
        page = await client.get("/admin/notifications")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "private, no-store"
        assert "Yakuniy xato" in page.text
        assert "Kutilmoqda" in page.text
        assert "••••6789" in page.text
        assert "123456789" not in page.text
        assert "synthetic-private-message-body" not in page.text
        assert "synthetic-private-detail" not in page.text
        assert "Tafsilot maxfiy saqlandi" in page.text
        assert "synthetic-waiting-body" not in page.text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

        response = await client.post(
            f"/admin/notifications/{failed.id}/retry",
            data={"csrf_token": csrf, "reason": "telegram_restored"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert f"retried={failed.id}" in response.headers["location"]
        await db_session.refresh(failed)
        assert failed.max_retries == 4
        assert failed.next_retry_at is not None
        scheduled = failed.next_retry_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        assert scheduled <= utcnow()

        audit = (await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "notification",
                AuditEvent.entity_id == str(failed.id),
            )
        )).scalar_one()
        assert audit.action == "notification_retried"
        assert audit.actor_id == str(admin.id)
        assert "synthetic-private-message-body" not in (audit.old_value or "")
        assert "synthetic-private-message-body" not in (audit.new_value or "")
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


@pytest.mark.asyncio
async def test_only_superadmin_can_open_notification_outbox(db_session):
    admin = AdminUser(
        username="notification-executor",
        password_hash="unused",
        full_name="Notification Executor",
        role=AdminRole.EXECUTOR.value,
    )
    db_session.add(admin)
    await db_session.flush()
    client = await _authorize_client(db_session, admin)
    try:
        response = await client.get("/admin/notifications")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


@pytest.mark.asyncio
async def test_manual_retry_rejects_active_automatic_retry(db_session):
    admin = AdminUser(
        username="notification-retry-admin",
        password_hash="unused",
        full_name="Retry Admin",
        role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.flush()
    notification = Notification(
        recipient_telegram_id=135791113,
        notification_type="response",
        message_text="synthetic-body",
        language="uz",
        retry_count=1,
        max_retries=3,
        next_retry_at=utcnow() + timedelta(hours=1),
    )
    db_session.add(notification)
    await db_session.commit()

    client = await _authorize_client(db_session, admin)
    try:
        page = await client.get("/admin/notifications?status=retrying")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = await client.post(
            f"/admin/notifications/{notification.id}/retry",
            data={"csrf_token": csrf, "reason": "telegram_restored"},
        )
        assert response.status_code == 409
        await db_session.refresh(notification)
        assert notification.max_retries == 3
        assert await db_session.scalar(select(AuditEvent.id).where(
            AuditEvent.entity_type == "notification"
        )) is None
    finally:
        app.dependency_overrides.clear()
        await client.aclose()
