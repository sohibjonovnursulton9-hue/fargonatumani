import pytest
import re
from datetime import timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text
from fastapi import HTTPException
from app.admin.app import app, assign_complaint, ensure_complaint_access, hash_session_token
from app.database import get_session
import app.database as database
from app.models import (
    AdminRole,
    AdminUser,
    Assignment,
    AdminSession,
    AuditEvent,
    CategoryOrganization,
    Category,
    Complaint,
    ComplaintLanguage,
    ComplaintType,
    Organization,
    MFYArea,
    User,
    Notification,
    ComplaintStatus,
    utcnow,
)
from app.services import ComplaintService


@pytest.mark.asyncio
async def test_assigning_new_complaint_updates_status_and_queues_citizen_notice(db_session, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.bot as bot_module

    citizen = User(telegram_id=87654321, interface_language="uz")
    category = Category(
        code="assignment-route-test", name_uz="Sinov yo‘nalishi",
        name_uz_cyrillic="Синов йўналиши", name_ru="Тестовое направление",
    )
    organization = Organization(
        code="assignment-route-org", name_uz="Sinov idorasi",
        name_uz_cyrillic="Синов идораси", name_ru="Тестовое ведомство",
    )
    admin = AdminUser(
        username="assignment-route-admin", password_hash="test-only",
        full_name="Synthetic Route Admin", role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add_all([citizen, category, organization, admin])
    await db_session.flush()
    db_session.add(CategoryOrganization(
        category_id=category.id, organization_id=organization.id, is_primary=True,
    ))
    await db_session.flush()
    complaint = await ComplaintService.create_complaint(
        session=db_session, user_id=citizen.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id, full_name="Synthetic Citizen",
        phone_number="+998901234567", title="Synthetic routing test",
        complaint_text="Synthetic test content only",
    )

    response = await assign_complaint(
        complaint.id, object(), organization.id, None, None, admin, db_session, None,
    )

    assert response.status_code == 303
    assert complaint.status == ComplaintStatus.ROUTED.value
    assignment = (await db_session.execute(
        select(Assignment).where(Assignment.complaint_id == complaint.id)
    )).scalar_one()
    assert assignment.organization_id == organization.id
    notices = (await db_session.execute(
        select(Notification).where(Notification.complaint_id == complaint.id)
    )).scalars().all()
    assert len(notices) == 1
    assert notices[0].notification_type == "complaint_routed"
    assert organization.name_uz in notices[0].message_text

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def test_session():
        yield db_session

    monkeypatch.setattr(bot_module, "get_session_factory", lambda: test_session)
    send_message = AsyncMock(return_value=SimpleNamespace(message_id=91))
    await bot_module.deliver_pending_notifications(SimpleNamespace(send_message=send_message))
    send_message.assert_awaited_once()
    assert send_message.await_args.kwargs["chat_id"] == citizen.telegram_id
    assert organization.name_uz in send_message.await_args.kwargs["text"]
    await db_session.refresh(notices[0])
    assert notices[0].sent is True

@pytest.mark.asyncio
async def test_admin_users_endpoint_protected(db_session):
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            # Without login
            resp = await client.get("/admin/users")
            assert resp.status_code == 303
            assert "/admin/login" in resp.headers["location"]

            # Post to create user should also be protected
            resp = await client.post("/admin/users", data={"username": "test", "full_name": "Test", "role": "super_admin"})
            assert resp.status_code == 303
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_login_persists_only_hashed_session_token(db_session):
    import bcrypt

    admin = AdminUser(
        username="synthetic-login-admin",
        password_hash=bcrypt.hashpw(b"synthetic-password", bcrypt.gensalt()).decode(),
        full_name="Synthetic Login Admin",
        role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.commit()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            page = await client.get("/admin/login")
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            response = await client.post(
                "/admin/login",
                data={"username": admin.username, "password": "synthetic-password", "csrf_token": csrf},
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert response.headers["cache-control"] == "private, no-store"
            raw_token = response.cookies["admin_session"]
            stored = (await db_session.execute(
                select(AdminSession).where(AdminSession.admin_user_id == admin.id)
            )).scalar_one()
            assert stored.session_token == hash_session_token(raw_token)
            assert stored.session_token != raw_token
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_stored_session_digest_is_not_accepted_as_browser_cookie(db_session):
    from starlette.requests import Request
    from app.admin.app import get_current_admin_optional

    admin = AdminUser(
        username="digest-guard-admin", password_hash="unused", full_name="Digest Guard",
        role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.flush()
    raw_token = "synthetic-session-token"
    digest = hash_session_token(raw_token)
    db_session.add(AdminSession(
        session_token=digest, admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=1), is_active=True,
    ))
    await db_session.commit()

    def request_for(cookie_value):
        return Request({
            "type": "http", "method": "GET", "path": "/admin/",
            "headers": [(b"cookie", f"admin_session={cookie_value}".encode())],
        })

    assert await get_current_admin_optional(request_for(digest), db_session) is None
    assert (await get_current_admin_optional(request_for(raw_token), db_session)).id == admin.id


@pytest.mark.asyncio
async def test_public_service_root_opens_admin_login():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        result = await client.get("/", follow_redirects=False)
        assert result.status_code == 302
        assert result.headers["location"] == "/admin/login"


@pytest.mark.asyncio
async def test_admin_lifespan_initializes_its_own_database():
    async with app.router.lifespan_context(app):
        assert database.engine is not None
        async with database.get_session_factory()() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    assert database.engine is None


@pytest.mark.asyncio
async def test_superadmin_can_manage_categories_and_orgs(db_session):
    admin = AdminUser(
        username="catalog-admin",
        password_hash="unused-in-route-test",
        full_name="Catalog Admin",
        role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.flush()
    legacy_session = AdminSession(
        session_token="catalog-session",
        admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=1),
        is_active=True,
    )
    db_session.add(legacy_session)
    await db_session.commit()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "catalog-session")
            page = await client.get("/admin/categories")
            assert page.status_code == 200
            assert legacy_session.session_token == hash_session_token("catalog-session")
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            headers = {"cookie": f"admin_session=catalog-session; csrf_token={csrf}"}

            created_category = await client.post(
                "/admin/categories",
                headers=headers,
                data={
                    "csrf_token": csrf,
                    "code": "new-water",
                    "name_uz": "Suv ta'minoti",
                    "name_uz_cyrillic": "Сув таъминоти",
                    "name_ru": "Водоснабжение",
                },
                follow_redirects=False,
            )
            assert created_category.status_code == 303
            created = (await db_session.execute(
                select(Category).where(Category.code == "new-water")
            )).scalar_one()
            assert created.name_uz == "Suv ta'minoti"

            updated_category = await client.post(
                f"/admin/categories/{created.id}",
                headers=headers,
                data={
                    "csrf_token": csrf,
                    "code": "new-water",
                    "name_uz": "Ichimlik suvi",
                    "name_uz_cyrillic": "Ичимлик суви",
                    "name_ru": "Питьевая вода",
                    "sort_order": "4",
                    "sla_days": "15",
                    "is_provisional": "false",
                },
                follow_redirects=False,
            )
            assert updated_category.status_code == 303
            await db_session.refresh(created)
            assert created.name_uz == "Ichimlik suvi"
            assert created.is_provisional is False

            created_org = await client.post(
                "/admin/organizations",
                headers=headers,
                data={
                    "csrf_token": csrf,
                    "code": "test-org-edit",
                    "name_uz": "Sinov idorasi",
                    "name_uz_cyrillic": "Синов идораси",
                    "name_ru": "Тестовое ведомство",
                },
                follow_redirects=False,
            )
            assert created_org.status_code == 303
            organization = (await db_session.execute(
                select(Organization).where(Organization.code == "test-org-edit")
            )).scalar_one()
            updated_org = await client.post(
                f"/admin/organizations/{organization.id}",
                headers=headers,
                data={
                    "csrf_token": csrf,
                    "code": organization.code,
                    "name_uz": "Yangilangan idora",
                    "name_uz_cyrillic": "Янгиланган идора",
                    "name_ru": "Обновленное ведомство",
                    "contact_info": "synthetic contact",
                    "is_provisional": "false",
                },
                follow_redirects=False,
            )
            assert updated_org.status_code == 303
            await db_session.refresh(organization)
            assert organization.name_uz == "Yangilangan idora"

            created_area = await client.post(
                "/admin/mfy",
                headers=headers,
                data={
                    "csrf_token": csrf,
                    "name_uz": "Sinov MFY",
                    "name_uz_cyrillic": "Синов МФЙ",
                    "name_ru": "Тестовая махалля",
                },
                follow_redirects=False,
            )
            assert created_area.status_code == 303
            area = (await db_session.execute(
                select(MFYArea).where(MFYArea.name_uz == "Sinov MFY")
            )).scalar_one()
            updated_area = await client.post(
                f"/admin/mfy/{area.id}",
                headers=headers,
                data={
                    "csrf_token": csrf,
                    "name_uz": "Sinov MFY yangilandi",
                    "name_uz_cyrillic": "Синов МФЙ янгиланди",
                    "name_ru": "Тестовая махалля обновлена",
                    "district": "Farg'ona tumani",
                    "sort_order": "6",
                    "is_provisional": "false",
                },
                follow_redirects=False,
            )
            assert updated_area.status_code == 303
            await db_session.refresh(area)
            assert area.name_uz == "Sinov MFY yangilandi"

            organizations_page = await client.get("/admin/organizations")
            assert organizations_page.status_code == 200
            assert "Mas’ul idoralar" in organizations_page.text
            mfy_page = await client.get("/admin/mfy")
            assert mfy_page.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_executor_can_only_access_active_assigned_complaints(db_session):
    organization = Organization(
        code="access-test-org",
        name_uz="Idora",
        name_uz_cyrillic="Идора",
        name_ru="Организация",
    )
    category = Category(
        code="access-test-category",
        name_uz="Soha",
        name_uz_cyrillic="Соҳа",
        name_ru="Сфера",
    )
    user = User(telegram_id=777001, interface_language="uz")
    supervisor = AdminUser(
        username="access-test-supervisor",
        password_hash="hash",
        full_name="Supervisor Test",
        role=AdminRole.SUPER_ADMIN.value,
    )
    executor = AdminUser(
        username="access-test-executor",
        password_hash="hash",
        full_name="Executor Test",
        role=AdminRole.EXECUTOR.value,
        organization=organization,
    )
    other_executor = AdminUser(
        username="access-test-other",
        password_hash="hash",
        full_name="Other Executor",
        role=AdminRole.EXECUTOR.value,
        organization=organization,
    )
    db_session.add_all([organization, category, user, supervisor, executor, other_executor])
    await db_session.flush()
    complaint = await ComplaintService.create_complaint(
        session=db_session,
        user_id=user.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id,
        full_name="Test Citizen",
        phone_number="+998901234567",
        title="Access test",
        complaint_text="Test body",
    )
    assignment = Assignment(
        complaint_id=complaint.id,
        organization_id=organization.id,
        assigned_to_id=executor.id,
        assigned_by_id=supervisor.id,
        is_active=True,
    )
    db_session.add(assignment)
    await db_session.flush()

    assert await ensure_complaint_access(db_session, complaint.id, executor) == complaint
    with pytest.raises(HTTPException) as denied:
        await ensure_complaint_access(db_session, complaint.id, other_executor)
    assert denied.value.status_code == 403

    assignment.is_active = False
    await db_session.flush()
    with pytest.raises(HTTPException) as revoked:
        await ensure_complaint_access(db_session, complaint.id, executor, write=True)
    assert revoked.value.status_code == 403


@pytest.mark.asyncio
async def test_supervisor_can_soft_delete_and_restore_complaint(db_session):
    admin = AdminUser(
        username="trash-supervisor",
        password_hash="unused-in-route-test",
        full_name="Synthetic Supervisor",
        role=AdminRole.SUPER_ADMIN.value,
    )
    executor = AdminUser(
        username="trash-executor",
        password_hash="unused-in-route-test",
        full_name="Synthetic Executor",
        role=AdminRole.EXECUTOR.value,
        organization_id=71,
    )
    user = User(telegram_id=777099, interface_language="uz")
    category = Category(
        code="trash-test-category",
        name_uz="Sinov yo‘nalishi",
        name_uz_cyrillic="Синов йўналиши",
        name_ru="Тестовое направление",
    )
    db_session.add_all([admin, executor, user, category])
    await db_session.flush()
    complaint = Complaint(
        tracking_id="FTMT-20260927-TRASHTEST01",
        user_id=user.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id,
        full_name="Synthetic Citizen",
        phone_number="+998900000099",
        title="Synthetic complaint",
        complaint_text="Synthetic body for an isolated test.",
    )
    db_session.add(complaint)
    db_session.add_all([
        AdminSession(
            session_token="trash-admin-session", admin_user_id=admin.id,
            expires_at=utcnow() + timedelta(hours=1), is_active=True,
        ),
        AdminSession(
            session_token="trash-executor-session", admin_user_id=executor.id,
            expires_at=utcnow() + timedelta(hours=1), is_active=True,
        ),
    ])
    await db_session.commit()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "trash-admin-session")
            active_page = await client.get("/admin/complaints")
            assert active_page.status_code == 200
            assert complaint.tracking_id in active_page.text
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', active_page.text).group(1)
            response = await client.post(
                f"/admin/complaints/{complaint.id}/delete",
                data={"csrf_token": csrf},
                follow_redirects=False,
            )
            assert response.status_code == 303
            await db_session.refresh(complaint)
            assert complaint.deleted_at is not None
            assert complaint.deleted_by_id == admin.id

            active_page = await client.get("/admin/complaints")
            assert complaint.tracking_id not in active_page.text
            trash_page = await client.get("/admin/complaints?deleted=true")
            assert trash_page.status_code == 200
            assert complaint.tracking_id in trash_page.text
            detail_page = await client.get(f"/admin/complaints/{complaint.id}?deleted=true")
            assert detail_page.status_code == 200
            assert "O‘chirilganlar bo‘limida" in detail_page.text

            client.cookies.set("admin_session", "trash-executor-session")
            denied = await client.post(
                f"/admin/complaints/{complaint.id}/delete",
                data={"csrf_token": csrf},
                follow_redirects=False,
            )
            assert denied.status_code == 403
            await db_session.refresh(complaint)
            assert complaint.deleted_at is not None

            client.cookies.set("admin_session", "trash-admin-session")
            restore_page = await client.get("/admin/complaints?deleted=true")
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', restore_page.text).group(1)
            restored = await client.post(
                f"/admin/complaints/{complaint.id}/restore-deleted",
                data={"csrf_token": csrf},
                follow_redirects=False,
            )
            assert restored.status_code == 303
            await db_session.refresh(complaint)
            assert complaint.deleted_at is None
            assert complaint.deleted_by_id is None
            active_page = await client.get("/admin/complaints")
            assert complaint.tracking_id in active_page.text

            events = (await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "complaint",
                    AuditEvent.entity_id == str(complaint.id),
                )
            )).scalars().all()
            assert {event.action for event in events} >= {
                "complaint_soft_deleted", "complaint_restored_from_trash",
            }
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_csrf_token_stays_valid_across_pages_and_user_edit_saves(db_session):
    admin = AdminUser(
        username="csrf-stability-admin",
        password_hash="unused-in-route-test",
        full_name="Synthetic CSRF Admin",
        role=AdminRole.SUPER_ADMIN.value,
    )
    target = AdminUser(
        username="csrf-stability-target",
        password_hash="unused-in-route-test",
        full_name="Synthetic Target",
        role=AdminRole.DISTRICT_SUPERVISOR.value,
    )
    db_session.add_all([admin, target])
    await db_session.flush()
    db_session.add(AdminSession(
        session_token="csrf-stability-session",
        admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=1),
        is_active=True,
    ))
    await db_session.commit()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "csrf-stability-session")
            tokens = []
            for path in [
                "/admin/categories", "/admin/organizations", "/admin/mfy",
                "/admin/users", "/admin/bot", "/admin/notifications",
            ]:
                response = await client.get(path)
                assert response.status_code == 200
                tokens.append(
                    re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
                )
            assert len(set(tokens)) == 1
            assert client.cookies.get("csrf_token") == tokens[0]
            updated = await client.post(
                f"/admin/users/{target.id}/edit",
                data={
                    "csrf_token": tokens[0],
                    "username": "csrf-stability-updated",
                    "full_name": "Updated Synthetic Target",
                    "role": AdminRole.AUDITOR.value,
                    "organization_id": "",
                    "telegram_id": "",
                },
                follow_redirects=False,
            )
            assert updated.status_code == 303
            await db_session.refresh(target)
            assert target.username == "csrf-stability-updated"
            assert target.role == AdminRole.AUDITOR.value
    finally:
        app.dependency_overrides.clear()
