"""Safe preview, import, and rollback tests using synthetic catalog rows only."""
import json
import re
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.admin.app import app
from app.database import get_session
from app.mfy_catalog import MFY_CATALOG, normalize_mfy_name, uzbek_latin_to_cyrillic
from app.models import AdminRole, AdminSession, AdminUser, AuditEvent, MFYArea, utcnow


def test_mfy_catalog_is_unique_and_transliterates_local_names():
    keys = [normalize_mfy_name(entry.name_uz) for entry in MFY_CATALOG]
    assert len(MFY_CATALOG) == 58
    assert len(set(keys)) == len(keys)
    assert uzbek_latin_to_cyrillic("Boʻston MFY") == "Бўстон МФЙ"
    assert uzbek_latin_to_cyrillic("Oʻzbekiston MFY") == "Ўзбекистон МФЙ"
    assert uzbek_latin_to_cyrillic("Yangi yoʻl MFY") == "Янги йўл МФЙ"
    assert normalize_mfy_name("Dōstlik MFY") == normalize_mfy_name("Doʻstlik MFY")
    assert all(entry.name_ru.startswith("МСГ ") for entry in MFY_CATALOG)


@pytest.mark.asyncio
async def test_superadmin_previews_imports_and_undoes_mfy_catalog(db_session):
    admin = AdminUser(
        username="synthetic-mfy-admin",
        password_hash="unused-in-route-test",
        full_name="Synthetic MFY Admin",
        role=AdminRole.SUPER_ADMIN.value,
    )
    approved = MFYArea(
        name_uz="Avval MFY", name_uz_cyrillic="old Cyrillic", name_ru="approved Russian",
        is_active=True, is_provisional=False,
    )
    provisional = MFYArea(
        name_uz="Bahor MFY", name_uz_cyrillic="old Bahor", name_ru="old Russian",
        is_active=True, is_provisional=True, sort_order=77,
    )
    stale_demo = MFYArea(
        name_uz="Old demo MFY", name_uz_cyrillic="Эски демо", name_ru="demo",
        is_active=True, is_provisional=True,
    )
    unrelated_official = MFYArea(
        name_uz="Official outside list MFY", name_uz_cyrillic="Расмий", name_ru="Официальный",
        is_active=True, is_provisional=False,
    )
    db_session.add_all([admin, approved, provisional, stale_demo, unrelated_official])
    await db_session.flush()
    session = AdminSession(
        session_token="synthetic-mfy-session",
        admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=1),
        is_active=True,
    )
    db_session.add(session)
    await db_session.commit()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("admin_session", "synthetic-mfy-session")
            page = await client.get("/admin/mfy/import")
            assert page.status_code == 200
            assert "58 ta nom bo‘yicha oldindan ko‘rish" in page.text
            assert "Hokimlik tasdiqlagan rasmiy ro‘yxat emas" in page.text
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

            rejected = await client.post(
                "/admin/mfy/import", data={"confirm_import": "yes"}, follow_redirects=False
            )
            assert rejected.status_code == 403

            response = await client.post(
                "/admin/mfy/import",
                data={
                    "csrf_token": csrf,
                    "confirm_import": "yes",
                    "deactivate_missing": "yes",
                },
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert response.headers["location"].endswith("imported=58")

            imported = (await db_session.execute(select(MFYArea))).scalars().all()
            active_by_key = {
                normalize_mfy_name(area.name_uz): area
                for area in imported if area.is_active
            }
            assert all(normalize_mfy_name(entry.name_uz) in active_by_key for entry in MFY_CATALOG)
            assert active_by_key[normalize_mfy_name("Avval MFY")].name_ru == "approved Russian"
            assert active_by_key[normalize_mfy_name("Avval MFY")].is_provisional is False
            assert active_by_key[normalize_mfy_name("Bahor MFY")].is_provisional is True
            assert active_by_key[normalize_mfy_name("Bahor MFY")].name_uz_cyrillic == "Баҳор МФЙ"
            assert not next(area for area in imported if area.name_uz == "Old demo MFY").is_active
            assert next(area for area in imported if area.name_uz == "Official outside list MFY").is_active

            event = (await db_session.execute(
                select(AuditEvent).where(AuditEvent.entity_type == "mfy_catalog_import")
            )).scalar_one()
            metadata = json.loads(event.metadata_json)
            assert event.actor_id == str(admin.id)
            assert metadata["source"] == "user_supplied_mfy_roster_scans"
            assert metadata["counts"]["created"] > 0
            assert metadata["counts"]["deactivated"] == 1
            assert len(json.loads(event.new_value)) > 0

            undo_page = await client.get("/admin/mfy/import")
            assert "Oxirgi importni qaytarish" in undo_page.text
            undo_csrf = re.search(r'name="csrf_token" value="([^"]+)"', undo_page.text).group(1)
            undone = await client.post(
                "/admin/mfy/import/undo",
                data={"csrf_token": undo_csrf, "event_id": str(event.id)},
                follow_redirects=False,
            )
            assert undone.status_code == 303
            await db_session.refresh(stale_demo)
            assert stale_demo.is_active is True
            await db_session.refresh(provisional)
            assert provisional.name_uz_cyrillic == "old Bahor"
            assert provisional.sort_order == 77
            new_bahor = (await db_session.execute(select(MFYArea).where(
                MFYArea.name_uz == "Bahor MFY",
                MFYArea.id != provisional.id,
            ))).scalars().first()
            assert new_bahor is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mfy_import_is_superadmin_only(db_session):
    admin = AdminUser(
        username="synthetic-mfy-executor",
        password_hash="unused-in-route-test",
        full_name="Synthetic Executor",
        role=AdminRole.EXECUTOR.value,
    )
    db_session.add(admin)
    await db_session.flush()
    db_session.add(AdminSession(
        session_token="synthetic-executor-session",
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
            client.cookies.set("admin_session", "synthetic-executor-session")
            page = await client.get("/admin/mfy/import")
            assert page.status_code == 403
    finally:
        app.dependency_overrides.clear()
