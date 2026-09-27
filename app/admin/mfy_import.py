"""Reviewable import of user-supplied MFY names into the area catalog."""
from __future__ import annotations

import json
from typing import Callable

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.admin.csrf import get_or_create_csrf_token
from app.mfy_catalog import MFY_CATALOG, MFY_SOURCE, normalize_mfy_name
from app.models import AdminUser, AuditAction, AuditEvent, MFYArea


def _state(area: MFYArea) -> dict:
    return {
        "name_uz": area.name_uz,
        "name_uz_cyrillic": area.name_uz_cyrillic,
        "name_ru": area.name_ru,
        "district": area.district,
        "is_active": area.is_active,
        "is_provisional": area.is_provisional,
        "sort_order": area.sort_order,
    }


def _snapshot(rows: list[dict]) -> str:
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def build_mfy_import_router(
    *, templates, admin_dependency: Callable, csrf_dependency: Callable,
    set_csrf_cookie: Callable[[Response, str], None],
) -> APIRouter:
    router = APIRouter()

    async def load_plan(db: AsyncSession):
        areas = (await db.execute(select(MFYArea).order_by(MFYArea.id))).scalars().all()
        by_key: dict[str, list[MFYArea]] = {}
        for area in areas:
            by_key.setdefault(normalize_mfy_name(area.name_uz), []).append(area)

        plan = []
        for order, entry in enumerate(MFY_CATALOG, start=1):
            matches = by_key.get(normalize_mfy_name(entry.name_uz), [])
            if len(matches) > 1:
                action = "duplicate"
                current = ", ".join(f"#{area.id}" for area in matches)
            elif not matches:
                action, current = "new", ""
            elif not matches[0].is_provisional:
                action = "official_inactive" if not matches[0].is_active else "official"
                current = matches[0].name_uz
            else:
                area = matches[0]
                proposed = {
                    "name_uz": entry.name_uz,
                    "name_uz_cyrillic": entry.name_uz_cyrillic,
                    "name_ru": entry.name_ru,
                    "district": "Farg'ona tumani",
                    "is_active": True,
                    "is_provisional": True,
                    "sort_order": order,
                }
                action = "update" if _state(area) != proposed else "same"
                if not area.is_active:
                    action = "reactivate"
                current = area.name_uz
            plan.append({"entry": entry, "action": action, "current": current, "order": order})

        target_keys = {normalize_mfy_name(entry.name_uz) for entry in MFY_CATALOG}
        retireable = [
            area for area in areas
            if area.is_active and area.is_provisional
            and normalize_mfy_name(area.name_uz) not in target_keys
        ]
        duplicates = [
            {"key": key, "ids": [area.id for area in matches]}
            for key, matches in by_key.items() if len(matches) > 1
        ]
        return areas, plan, retireable, duplicates

    @router.get("/admin/mfy/import", response_class=HTMLResponse)
    async def preview_mfy_import(
        request: Request,
        admin: AdminUser = Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
    ):
        _, plan, retireable, duplicates = await load_plan(db)
        csrf_token = get_or_create_csrf_token(request)
        latest_event = await db.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_type == "mfy_catalog_import",
                AuditEvent.action == AuditAction.CONFIG_CHANGED.value,
            ).order_by(AuditEvent.id.desc()).limit(1)
        )
        undo_event_id = None
        if latest_event and json.loads(latest_event.metadata_json or "{}").get("operation") == "import":
            undo_event_id = latest_event.id
        response = templates.TemplateResponse(request=request, name="admin/mfy_import.html", context={
            "request": request,
            "admin": admin,
            "csrf_token": csrf_token,
            "plan": plan,
            "retireable": retireable,
            "duplicates": duplicates,
            "source": MFY_SOURCE,
            "undo_event_id": undo_event_id,
            "success": request.query_params.get("imported"),
        })
        set_csrf_cookie(response, csrf_token)
        return response

    @router.post("/admin/mfy/import")
    async def apply_mfy_import(
        confirm_import: str = Form(...),
        deactivate_missing: str | None = Form(None),
        admin: AdminUser = Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        if confirm_import != "yes":
            raise HTTPException(status_code=400, detail="Importni tasdiqlash talab qilinadi")
        _, plan, retireable, duplicates = await load_plan(db)
        if duplicates:
            raise HTTPException(status_code=409, detail="MFY katalogida takroriy yozuvlar bor")

        before: list[dict] = []
        after: list[dict] = []
        counts = {"created": 0, "updated": 0, "reactivated": 0, "official_preserved": 0, "deactivated": 0}
        all_areas = (await db.execute(select(MFYArea))).scalars().all()
        by_key = {normalize_mfy_name(area.name_uz): area for area in all_areas}

        for item in plan:
            entry, action, order = item["entry"], item["action"], item["order"]
            key = normalize_mfy_name(entry.name_uz)
            area = by_key.get(key)
            if area is not None and not area.is_provisional:
                counts["official_preserved"] += 1
                continue
            if area is None:
                area = MFYArea(
                    name_uz=entry.name_uz,
                    name_uz_cyrillic=entry.name_uz_cyrillic,
                    name_ru=entry.name_ru,
                    district="Farg'ona tumani",
                    is_active=True,
                    is_provisional=True,
                    sort_order=order,
                )
                db.add(area)
                await db.flush()
                before.append({"id": area.id, "state": None})
                counts["created"] += 1
            else:
                old = _state(area)
                proposed = {
                    "name_uz": entry.name_uz,
                    "name_uz_cyrillic": entry.name_uz_cyrillic,
                    "name_ru": entry.name_ru,
                    "district": "Farg'ona tumani",
                    "is_active": True,
                    "is_provisional": True,
                    "sort_order": order,
                }
                if old == proposed:
                    continue
                before.append({"id": area.id, "state": old})
                was_inactive = not area.is_active
                for field, value in proposed.items():
                    setattr(area, field, value)
                counts["reactivated" if was_inactive else "updated"] += 1
            after.append({"id": area.id, "state": _state(area)})

        if deactivate_missing == "yes":
            for area in retireable:
                before.append({"id": area.id, "state": _state(area)})
                area.is_active = False
                after.append({"id": area.id, "state": _state(area)})
                counts["deactivated"] += 1

        event = AuditEvent(
            action=AuditAction.CONFIG_CHANGED.value,
            entity_type="mfy_catalog_import",
            actor_type="admin",
            actor_id=str(admin.id),
            old_value=_snapshot(before),
            new_value=_snapshot(after),
            metadata_json=json.dumps({
                "operation": "import",
                "source": MFY_SOURCE,
                "provisional_source": True,
                "counts": counts,
            }, ensure_ascii=False),
        )
        db.add(event)
        await db.commit()
        return RedirectResponse(url=f"/admin/mfy/import?imported={len(MFY_CATALOG)}", status_code=303)

    @router.post("/admin/mfy/import/undo")
    async def undo_mfy_import(
        event_id: int = Form(...),
        admin: AdminUser = Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        event = await db.scalar(select(AuditEvent).where(
            AuditEvent.id == event_id,
            AuditEvent.entity_type == "mfy_catalog_import",
            AuditEvent.action == AuditAction.CONFIG_CHANGED.value,
        ))
        if event is None:
            raise HTTPException(status_code=404)
        meta = json.loads(event.metadata_json or "{}")
        if meta.get("operation") != "import":
            raise HTTPException(status_code=409, detail="Faqat oxirgi importni qaytarish mumkin")
        latest = await db.scalar(select(AuditEvent.id).where(
            AuditEvent.entity_type == "mfy_catalog_import",
            AuditEvent.action == AuditAction.CONFIG_CHANGED.value,
        ).order_by(AuditEvent.id.desc()).limit(1))
        if latest != event.id:
            raise HTTPException(status_code=409, detail="Avvalgi importga keyinroq o‘zgarish kiritilgan")

        before_rows = json.loads(event.old_value or "[]")
        after_rows = json.loads(event.new_value or "[]")
        areas = {area.id: area for area in (await db.execute(select(MFYArea))).scalars().all()}
        for item in after_rows:
            area = areas.get(item["id"])
            if area is None or _state(area) != item["state"]:
                raise HTTPException(status_code=409, detail="Importdan keyin MFY yozuvi o‘zgargan; qaytarish bekor qilindi")

        old_by_id = {item["id"]: item["state"] for item in before_rows}
        undo_before, undo_after = [], []
        for item in after_rows:
            area = areas[item["id"]]
            undo_before.append({"id": area.id, "state": _state(area)})
            old = old_by_id.get(area.id)
            if old is None:
                area.is_active = False
            else:
                for field, value in old.items():
                    setattr(area, field, value)
            undo_after.append({"id": area.id, "state": _state(area)})

        undo_event = AuditEvent(
            action=AuditAction.CONFIG_CHANGED.value,
            entity_type="mfy_catalog_import",
            actor_type="admin",
            actor_id=str(admin.id),
            old_value=_snapshot(undo_before),
            new_value=_snapshot(undo_after),
            metadata_json=json.dumps({"operation": "undo", "undo_of": event.id}, ensure_ascii=False),
        )
        db.add(undo_event)
        await db.commit()
        return RedirectResponse(url="/admin/mfy/import?imported=0", status_code=303)

    return router
