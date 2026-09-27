import re
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.admin.app import app, hash_session_token
from app.database import get_session
from app.models import (
    AdminRole, AdminSession, AdminUser, Attachment, Assignment, AuditEvent, Category,
    CategoryOrganization, ComplaintLanguage, ComplaintStatus, ComplaintType,
    Notification, Organization, User, utcnow,
)
from app.services import ComplaintService
from app.change_models import ComplaintCitizenMessage, ComplaintInternalNote
from app.citizen_info import CitizenInfoService
import app.admin.complaint_tools as complaint_tools


def _fake_bot():
    class RemoteFile:
        file_size = 4
        async def download_as_bytearray(self):
            return bytearray(b"test")

    class FakeBot:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def get_file(self, _file_id):
            return RemoteFile()
    return FakeBot()


@pytest.mark.asyncio
async def test_assigned_staff_can_add_private_note_and_view_attachment_but_other_executor_cannot(db_session, monkeypatch):
    org = Organization(code="tools-org", name_uz="Idora", name_uz_cyrillic="Идора", name_ru="Организация")
    category = Category(code="tools-cat", name_uz="Yo‘nalish", name_uz_cyrillic="Йўналиш", name_ru="Направление")
    user = User(telegram_id=778801, interface_language="uz")
    supervisor = AdminUser(username="tools-supervisor", password_hash="test", full_name="Supervisor", role=AdminRole.SUPER_ADMIN.value)
    executor = AdminUser(username="tools-executor", password_hash="test", full_name="Assigned Worker", role=AdminRole.EXECUTOR.value, organization=org)
    other = AdminUser(username="tools-other", password_hash="test", full_name="Other Worker", role=AdminRole.EXECUTOR.value, organization=org)
    db_session.add_all([org, category, user, supervisor, executor, other])
    await db_session.flush()
    db_session.add(CategoryOrganization(category_id=category.id, organization_id=org.id))
    complaint = await ComplaintService.create_complaint(
        session=db_session, user_id=user.id, complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value, category_id=category.id,
        full_name="Synthetic Citizen", phone_number="+998900000001",
        title="Synthetic case", complaint_text="Synthetic test body",
    )
    db_session.add_all([
        Assignment(complaint_id=complaint.id, organization_id=org.id, assigned_to_id=executor.id, assigned_by_id=supervisor.id),
        Attachment(complaint_id=complaint.id, file_type="document", file_name="sample.pdf",
                   file_size=4, telegram_file_id="fake-file-id", mime_type="application/pdf"),
        AdminSession(session_token="tools-executor-session", admin_user_id=executor.id,
                     expires_at=utcnow() + timedelta(hours=1), is_active=True),
        AdminSession(session_token="tools-other-session", admin_user_id=other.id,
                     expires_at=utcnow() + timedelta(hours=1), is_active=True),
    ])
    await db_session.commit()

    async def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr(complaint_tools, "Bot", lambda token: _fake_bot())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "tools-executor-session")
            page = await client.get(f"/admin/complaints/{complaint.id}")
            assert page.status_code == 200
            assert "Xodimlar izohlari" in page.text
            assert "sample.pdf" in page.text
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            authorized_cookies = f"admin_session=tools-executor-session; csrf_token={csrf}"
            note_response = await client.post(
                f"/admin/complaints/{complaint.id}/internal-notes",
                headers={"cookie": authorized_cookies},
                data={"csrf_token": csrf, "body": "Internal coordination note"},
                follow_redirects=False,
            )
            assert note_response.status_code == 303
            note = (await db_session.execute(select(ComplaintInternalNote))).scalar_one()
            assert note.body == "Internal coordination note"

            file_response = await client.get(
                "/admin/attachments/1",
                headers={"cookie": authorized_cookies},
            )
            assert file_response.status_code == 200
            assert file_response.content == b"test"
            assert file_response.headers["cache-control"] == "private, no-store"
            assert file_response.headers["x-content-type-options"] == "nosniff"

            denied_cookies = f"admin_session=tools-other-session; csrf_token={csrf}"
            denied = await client.post(
                f"/admin/complaints/{complaint.id}/internal-notes",
                headers={"cookie": denied_cookies},
                data={"csrf_token": csrf, "body": "Must not be saved"},
                follow_redirects=False,
            )
            assert denied.status_code == 403
            denied_file = await client.get("/admin/attachments/1", headers={"cookie": denied_cookies})
            assert denied_file.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_login_and_dashboard_render_accessible_login_and_real_empty_states(db_session):
    async def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            login = await client.get("/admin/login")
            assert login.status_code == 200
            assert login.headers["cache-control"] == "private, no-store"
            assert login.headers["pragma"] == "no-cache"
            assert "Admin login" in login.text
            assert 'id="password-toggle"' in login.text
            assert 'class="auth-card"' in login.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_empty_dashboard_and_bot_settings_render_without_demo_counts(db_session):
    admin = AdminUser(
        username="dashboard-test-admin", password_hash="test",
        full_name="Dashboard Test", role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.flush()
    db_session.add(AdminSession(
        session_token="dashboard-test-session", admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=1), is_active=True,
    ))
    await db_session.commit()

    async def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "dashboard-test-session")
            dashboard = await client.get("/admin/")
            assert dashboard.status_code == 200
            assert dashboard.headers["cache-control"] == "private, no-store"
            assert "0 ta" in dashboard.text
            assert "Hozircha faol murojaat yo‘q" in dashboard.text
            assert '<svg class="nav-icon"' in dashboard.text
            assert 'aria-current="page"' in dashboard.text
            statistics = await client.get("/admin/statistics")
            assert statistics.status_code == 200
            assert "Hozircha statistika yo‘q" in statistics.text
            assert "Jonli ma’lumotlar" in statistics.text
            settings = await client.get("/admin/bot")
            assert settings.status_code == 200
            assert "Faol versiya va qoralama" in settings.text
            assert "Sozlamalar hozircha faqat qoralama" in settings.text
            assert "Tahrirlanadigan matn" in settings.text
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', settings.text).group(1)
            cookies = f"admin_session=dashboard-test-session; csrf_token={csrf}"
            saved = await client.post(
                "/admin/bot/text-overrides",
                headers={"cookie": cookies},
                data={
                    "csrf_token": csrf,
                    "text_key": "main_menu",
                    "text_uz": "Test Uzbek Latin",
                    "text_uz_cyrillic": "Test Uzbek Cyrillic",
                    "text_ru": "Test Russian",
                },
                follow_redirects=False,
            )
            assert saved.status_code == 303
            from app.bot_config import read_admin_bot_state
            config_state = await read_admin_bot_state(db_session)
            assert config_state["draft_config"]["translation_overrides"]["main_menu"]["uz"] == "Test Uzbek Latin"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_citizen_additional_info_round_trip_is_owner_checked_and_audited(db_session, monkeypatch):
    from app import bot as bot_module

    org = Organization(
        code="info-flow-org", name_uz="Test idora",
        name_uz_cyrillic="Тест идора", name_ru="Тестовый отдел",
    )
    category = Category(
        code="info-flow-category", name_uz="Test yo‘nalish",
        name_uz_cyrillic="Тест йўналиш", name_ru="Тестовая категория",
    )
    user = User(telegram_id=778901, interface_language="uz")
    other_user = User(telegram_id=778902, interface_language="ru")
    supervisor = AdminUser(
        username="info-flow-supervisor", password_hash="test",
        full_name="Synthetic Supervisor", role=AdminRole.SUPER_ADMIN.value,
    )
    executor = AdminUser(
        username="info-flow-executor", password_hash="test",
        full_name="Synthetic Executor", role=AdminRole.EXECUTOR.value,
        organization=org, telegram_id=778903,
    )
    db_session.add_all([org, category, user, other_user, supervisor, executor])
    await db_session.flush()
    db_session.add(CategoryOrganization(category_id=category.id, organization_id=org.id))
    complaint = await ComplaintService.create_complaint(
        session=db_session, user_id=user.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id, full_name="Synthetic Citizen",
        phone_number="+998900000901", title="Synthetic info case",
        complaint_text="Synthetic test body",
    )
    for status in (
        ComplaintStatus.TRIAGE,
        ComplaintStatus.ROUTED,
        ComplaintStatus.IN_PROGRESS,
    ):
        await ComplaintService.transition_status(
            db_session, complaint.id, status, "admin", str(supervisor.id)
        )
    db_session.add_all([
        Assignment(
            complaint_id=complaint.id, organization_id=org.id,
            assigned_to_id=executor.id, assigned_by_id=supervisor.id,
        ),
        AdminSession(
            session_token="info-flow-admin-session", admin_user_id=supervisor.id,
            expires_at=utcnow() + timedelta(hours=1), is_active=True,
        ),
    ])
    await db_session.commit()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "info-flow-admin-session")
            page = await client.get(f"/admin/complaints/{complaint.id}")
            assert page.status_code == 200
            assert "Fuqaro yuborishi kerak bo‘lgan ma’lumot" in page.text
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            request_response = await client.post(
                f"/admin/complaints/{complaint.id}/request-info",
                headers={"cookie": f"admin_session=info-flow-admin-session; csrf_token={csrf}"},
                data={"csrf_token": csrf, "request_text": "Synthetic requested document"},
                follow_redirects=False,
            )
            assert request_response.status_code == 303
    finally:
        app.dependency_overrides.clear()

    assert complaint.status == ComplaintStatus.WAITING_FOR_CITIZEN.value
    request_message = (await db_session.execute(
        select(ComplaintCitizenMessage).where(
            ComplaintCitizenMessage.message_type == "request"
        )
    )).scalar_one()
    assert request_message.body == "Synthetic requested document"
    with pytest.raises(ValueError, match="tegishli emas"):
        await CitizenInfoService.submit_citizen_reply(
            db_session, complaint.id, request_message.id, other_user.id,
            "Unauthorized synthetic reply",
        )

    @asynccontextmanager
    async def session_context():
        yield db_session

    monkeypatch.setattr(
        bot_module, "get_session_factory", lambda: lambda: session_context()
    )
    send_message = AsyncMock(return_value=SimpleNamespace(message_id=37))
    await bot_module.deliver_pending_notifications(
        SimpleNamespace(send_message=send_message)
    )
    request_call = next(
        call for call in send_message.await_args_list
        if call.kwargs["chat_id"] == user.telegram_id
        and call.kwargs["reply_markup"] is not None
        and call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        == f"additional_info_{complaint.id}"
    )
    assert request_message.body in request_call.kwargs["text"]

    async def start_callback(telegram_id):
        query = SimpleNamespace(
            data=f"additional_info_{complaint.id}",
            from_user=SimpleNamespace(id=telegram_id),
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(user_data={})
        state = await bot_module.start_additional_info(
            SimpleNamespace(callback_query=query), context
        )
        return state, context, query

    denied_state, _, denied_query = await start_callback(other_user.telegram_id)
    assert denied_state == bot_module.ConversationHandler.END
    assert denied_query.answer.await_args.kwargs["show_alert"] is True

    state, context, _ = await start_callback(user.telegram_id)
    assert state == bot_module.ADDITIONAL_INFO
    assert context.user_data["additional_info_request_id"] == request_message.id

    citizen_message = SimpleNamespace(
        text=None,
        caption="Synthetic photo note",
        photo=[SimpleNamespace(file_id="synthetic-telegram-file", file_size=4)],
        document=None,
        video=None,
        reply_text=AsyncMock(),
    )
    reply_update = SimpleNamespace(
        effective_message=citizen_message,
        effective_user=SimpleNamespace(id=user.telegram_id),
    )
    reply_state = await bot_module.receive_additional_info(reply_update, context)
    assert reply_state == bot_module.ConversationHandler.END
    assert "qabul qilindi" in citizen_message.reply_text.await_args.args[0]
    assert complaint.status == ComplaintStatus.IN_PROGRESS.value

    reply_message = (await db_session.execute(
        select(ComplaintCitizenMessage).where(
            ComplaintCitizenMessage.message_type == "reply"
        )
    )).scalar_one()
    assert reply_message.reply_to_id == request_message.id
    assert reply_message.author_id == user.id
    assert reply_message.body == "Synthetic photo note"
    file = (await db_session.execute(
        select(Attachment).where(Attachment.message_id == reply_message.id)
    )).scalar_one()
    assert file.telegram_file_id == "synthetic-telegram-file"

    staff_notice = (await db_session.execute(
        select(Notification).where(
            Notification.notification_type == "citizen_additional_info"
        )
    )).scalar_one()
    assert staff_notice.recipient_telegram_id == executor.telegram_id
    assert "Synthetic photo note" not in staff_notice.message_text
    assert (await db_session.execute(
        select(AuditEvent).where(AuditEvent.action == "citizen_info_received")
    )).scalar_one()


@pytest.mark.asyncio
async def test_statistics_chart_uses_synthetic_database_counts_and_resolution_time(db_session):
    admin = AdminUser(
        username="statistics-test-admin", password_hash="test",
        full_name="Statistics Test", role=AdminRole.SUPER_ADMIN.value,
    )
    user = User(telegram_id=779991, interface_language="uz")
    category = Category(
        code="statistics-test-category", name_uz="Test yo‘nalish",
        name_uz_cyrillic="Тест йўналиш", name_ru="Тестовое направление",
    )
    db_session.add_all([admin, user, category])
    await db_session.flush()
    complaint = await ComplaintService.create_complaint(
        session=db_session, user_id=user.id, complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value, category_id=category.id,
        full_name="Synthetic Citizen", phone_number="+998900000099",
        title="Synthetic statistics case", complaint_text="Synthetic test data",
    )
    complaint.status = ComplaintStatus.RESOLVED.value
    complaint.resolved_at = complaint.submitted_at + timedelta(hours=26)
    db_session.add(AdminSession(
        session_token="statistics-test-session", admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=1), is_active=True,
    ))
    await db_session.commit()

    async def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "statistics-test-session")
            response = await client.get("/admin/statistics")
            assert response.status_code == 200
            assert "chart-bar--resolved" in response.text
            assert "Jami murojaat" in response.text
            assert "26.0" in response.text
            assert "Synthetic Citizen" not in response.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_superadmin_can_edit_staff_role_and_organization_with_audit(db_session):
    first_org = Organization(
        code="edit-user-first", name_uz="Birinchi idora",
        name_uz_cyrillic="Биринчи идора", name_ru="Первый отдел",
    )
    next_org = Organization(
        code="edit-user-next", name_uz="Ikkinchi idora",
        name_uz_cyrillic="Иккинчи идора", name_ru="Второй отдел",
    )
    admin = AdminUser(
        username="edit-user-superadmin", password_hash="test",
        full_name="Super Admin", role=AdminRole.SUPER_ADMIN.value,
    )
    target = AdminUser(
        username="old-executor", password_hash="test",
        full_name="Old Executor", role=AdminRole.EXECUTOR.value,
        organization=first_org,
    )
    db_session.add_all([first_org, next_org, admin, target])
    await db_session.flush()
    db_session.add(AdminSession(
        session_token="edit-user-session", admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=1), is_active=True,
    ))
    await db_session.commit()

    async def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "edit-user-session")
            listing = await client.get("/admin/users")
            assert listing.status_code == 200
            assert "Tahrirlash" in listing.text
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', listing.text).group(1)
            cookies = f"admin_session=edit-user-session; csrf_token={csrf}"
            response = await client.post(
                f"/admin/users/{target.id}/edit",
                headers={"cookie": cookies},
                data={
                    "csrf_token": csrf, "username": "new-agency-head",
                    "full_name": "Updated Staff Member", "role": AdminRole.AGENCY_HEAD.value,
                    "organization_id": str(next_org.id), "telegram_id": "779900",
                },
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert target.username == "new-agency-head"
            assert target.role == AdminRole.AGENCY_HEAD.value
            assert target.organization_id == next_org.id
            audit = (await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "admin_user_updated")
            )).scalar_one()
            assert '"role": "agency_head"' in audit.new_value
            assert '"organization_id": ' + str(next_org.id) in audit.new_value

            denied_self_change = await client.post(
                f"/admin/users/{admin.id}/edit",
                headers={"cookie": cookies},
                data={
                    "csrf_token": csrf, "username": admin.username,
                    "full_name": admin.full_name, "role": AdminRole.AUDITOR.value,
                },
                follow_redirects=False,
            )
            assert denied_self_change.status_code == 400
            assert admin.role == AdminRole.SUPER_ADMIN.value
    finally:
        app.dependency_overrides.clear()
