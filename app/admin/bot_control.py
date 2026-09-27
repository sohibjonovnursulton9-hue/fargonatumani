"""Admin routes for staged, audited citizen-facing bot configuration."""
from __future__ import annotations

import json
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot_config import (
    LOCALES,
    read_admin_bot_state,
    save_bot_draft,
    mark_bot_revision_previewed,
    publish_bot_revision,
    rollback_bot_revision,
)
from app.database import get_session
from app.admin.csrf import get_or_create_csrf_token
from app.i18n import TEXTS


PROTECTED_TEXT_KEYS = {key for key in TEXTS if key.startswith("consent_")}
TRANSLATION_KEYS = {
    key: key.replace("_", " ").title()
    for key in TEXTS if key not in PROTECTED_TEXT_KEYS
}

TEXT_PREFIXES = (
    "maintenance_message",
    "welcome_message",
    "help_message",
    "announcement",
    "working_hours",
    "contact_info",
    "deadline_reminder_message",
    "confirmation_reminder_message",
    "escalation_message",
)


def _form_bool(form, key: str) -> bool:
    return str(form.get(key, "")).lower() in {"true", "1", "on", "yes"}


async def _edit_config(session: AsyncSession) -> dict:
    state = await read_admin_bot_state(session)
    return state["draft_config"]


async def _save_config(session: AsyncSession, admin, config: dict, note: str | None):
    revision = await save_bot_draft(session, config, admin.id, note)
    await session.commit()
    return revision


def build_bot_control_router(
    templates,
    admin_dependency,
    csrf_dependency,
    set_csrf_cookie,
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/bot", response_class=HTMLResponse)
    async def bot_settings_page(
        request: Request,
        saved: int = 0,
        previewed: int | None = None,
        error: str | None = None,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
    ):
        state = await read_admin_bot_state(db)
        draft = state["draft"]
        preview_config = None
        if draft and previewed == draft.id and draft.previewed_at:
            preview_config = state["draft_config"]
        csrf_token = get_or_create_csrf_token(request)
        response = templates.TemplateResponse(
            request=request,
            name="admin/bot.html",
            context={
                "request": request,
                "admin": admin,
                "csrf_token": csrf_token,
                "config": state["draft_config"],
                "active_config": state["active_config"],
                "active_revision_id": state["active_revision_id"],
                "draft": draft,
                "preview_config": preview_config,
                "revisions": state["revisions"],
                "saved": saved == 1,
                "error": error[:200] if error else None,
                "translation_keys": sorted(TRANSLATION_KEYS.items(), key=lambda item: item[1]),
            },
        )
        response.headers["Cache-Control"] = "no-store, private"
        set_csrf_cookie(response, csrf_token)
        return response

    @router.post("/admin/bot")
    async def save_bot_settings(
        request: Request,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        form = await request.form()
        config = await _edit_config(db)
        for prefix in TEXT_PREFIXES:
            for locale in LOCALES:
                key = f"{prefix}_{locale}"
                value = str(form.get(key, "")).strip()
                if len(value) > 4000:
                    return RedirectResponse(
                        f"/admin/bot?error={quote('Har bir xabar 4000 belgidan oshmasin')}",
                        status_code=303,
                    )
                config[key] = value

        config["maintenance_enabled"] = _form_bool(form, "maintenance_enabled")
        config["announcement_enabled"] = _form_bool(form, "announcement_enabled")
        try:
            daily_limit = int(form.get("daily_limit", ""))
            ariza_days = int(form.get("ariza_days", ""))
            shikoyat_days = int(form.get("shikoyat_days", ""))
            taklif_days = int(form.get("taklif_days", ""))
            reminder_days = int(form.get("reminder_days_before", ""))
            escalation_days = int(form.get("escalate_after_days", ""))
            confirmation_reminder_hours = int(form.get("confirmation_reminder_hours", ""))
        except (TypeError, ValueError):
            return RedirectResponse("/admin/bot?error=Kun%20va%20limit%20qiymatlarini%20tekshiring", status_code=303)
        if not 1 <= daily_limit <= 20:
            return RedirectResponse("/admin/bot?error=Kunlik%20limit%201-20%20oralig%E2%80%98ida", status_code=303)
        if not 1 <= reminder_days <= 30 or not 1 <= escalation_days <= 365:
            return RedirectResponse("/admin/bot?error=Eslatma%20kunlarini%20tekshiring", status_code=303)
        if any(not 1 <= days <= 365 for days in (ariza_days, shikoyat_days, taklif_days)):
            return RedirectResponse("/admin/bot?error=Murojaat%20muddatlarini%20tekshiring", status_code=303)
        if not 1 <= confirmation_reminder_hours <= 336:
            return RedirectResponse("/admin/bot?error=Fuqaro%20eslatmasi%20muddatini%20tekshiring", status_code=303)

        config["daily_limit"] = daily_limit
        config["features"] = {
            "submissions": _form_bool(form, "submissions_enabled"),
            "my_complaints": _form_bool(form, "my_complaints_enabled"),
            "help": _form_bool(form, "help_enabled"),
        }
        fields = config["form_fields"]
        for field_name in ("full_name", "phone", "mfy", "address", "title", "description", "attachments"):
            for locale in LOCALES:
                value = str(form.get(f"hint_{field_name}_{locale}", "")).strip()
                if len(value) > 500:
                    return RedirectResponse("/admin/bot?error=Maydon%20izohi%20500%20belgidan%20oshmasin", status_code=303)
                config["field_hints"][field_name][locale] = value
        for field in ("mfy", "address", "attachments"):
            if field in {"mfy", "address"}:
                fields[field] = {"enabled": True, "required": True}
                continue
            enabled = _form_bool(form, f"{field}_enabled")
            fields[field] = {
                "enabled": enabled,
                "required": enabled and _form_bool(form, f"{field}_required"),
            }
        config["deadline_policy"] = {
            "ariza_days": ariza_days,
            "shikoyat_days": shikoyat_days,
            "taklif_days": taklif_days,
            "reminder_days_before": reminder_days,
            "escalate_after_days": escalation_days,
            "confirmation_reminder_hours": confirmation_reminder_hours,
        }
        note = str(form.get("revision_note", "")).strip()
        try:
            await _save_config(db, admin, config, note)
        except ValueError as exc:
            return RedirectResponse(f"/admin/bot?error={quote(str(exc))}", status_code=303)
        return RedirectResponse("/admin/bot?outcome=draft", status_code=303)

    @router.post("/admin/bot/text-overrides")
    async def add_text_override(
        request: Request,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        form = await request.form()
        key = str(form.get("text_key", "")).strip()
        if key not in TRANSLATION_KEYS:
            raise HTTPException(status_code=400, detail="Matn kaliti ro‘yxatda yo‘q.")
        translations = {}
        for locale in LOCALES:
            value = str(form.get(f"text_{locale}", "")).strip()
            if not value or len(value) > 3500:
                raise HTTPException(status_code=400, detail="Har bir tilda matn 1-3500 belgi oralig‘ida bo‘lsin.")
            translations[locale] = value
        config = await _edit_config(db)
        config["translation_overrides"][key] = translations
        await _save_config(db, admin, config, f"Bot matni o‘zgardi: {key}")
        return RedirectResponse("/admin/bot?outcome=draft", status_code=303)

    @router.post("/admin/bot/text-overrides/{key}/delete")
    async def delete_text_override(
        key: str,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        config = await _edit_config(db)
        if key not in config["translation_overrides"]:
            raise HTTPException(status_code=404, detail="Matn sozlamasi topilmadi.")
        del config["translation_overrides"][key]
        await _save_config(db, admin, config, f"Bot matni standartga qaytdi: {key}")
        return RedirectResponse("/admin/bot?outcome=draft", status_code=303)

    @router.post("/admin/bot/quick-answers")
    async def add_quick_answer(
        request: Request,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        form = await request.form()
        config = await _edit_config(db)
        if len(config["quick_answers"]) >= 12:
            raise HTTPException(status_code=400, detail="Ko‘pi bilan 12 ta tezkor javob mumkin.")
        item = {"id": uuid.uuid4().hex, "enabled": True}
        for locale in LOCALES:
            item[f"label_{locale}"] = str(form.get(f"label_{locale}", "")).strip()
            item[f"response_{locale}"] = str(form.get(f"response_{locale}", "")).strip()
            if not item[f"label_{locale}"] or len(item[f"label_{locale}"]) > 32:
                raise HTTPException(status_code=400, detail="Har uch tilda tugma nomini 1-32 belgida kiriting.")
            if not item[f"response_{locale}"] or len(item[f"response_{locale}"]) > 3500:
                raise HTTPException(status_code=400, detail="Har uch tilda javob 1-3500 belgida bo‘lsin.")
        config["quick_answers"].append(item)
        await _save_config(db, admin, config, "Tezkor javob qo‘shildi")
        return RedirectResponse("/admin/bot?outcome=draft", status_code=303)

    @router.post("/admin/bot/quick-answers/{item_id}/toggle")
    async def toggle_quick_answer(
        item_id: str,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        config = await _edit_config(db)
        item = next((item for item in config["quick_answers"] if item["id"] == item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Tezkor javob topilmadi.")
        item["enabled"] = not item["enabled"]
        await _save_config(db, admin, config, "Tezkor javob holati o‘zgardi")
        return RedirectResponse("/admin/bot?outcome=draft", status_code=303)

    @router.post("/admin/bot/quick-answers/{item_id}/delete")
    async def delete_quick_answer(
        item_id: str,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        config = await _edit_config(db)
        filtered = [item for item in config["quick_answers"] if item["id"] != item_id]
        if len(filtered) == len(config["quick_answers"]):
            raise HTTPException(status_code=404, detail="Tezkor javob topilmadi.")
        config["quick_answers"] = filtered
        await _save_config(db, admin, config, "Tezkor javob o‘chirildi")
        return RedirectResponse("/admin/bot?outcome=draft", status_code=303)

    @router.post("/admin/bot/response-templates")
    async def add_response_template(
        request: Request,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        form = await request.form()
        config = await _edit_config(db)
        if len(config["response_templates"]) >= 20:
            raise HTTPException(status_code=400, detail="Ko‘pi bilan 20 ta javob shabloni mumkin.")
        item = {"id": uuid.uuid4().hex, "enabled": True}
        for locale in LOCALES:
            item[f"label_{locale}"] = str(form.get(f"label_{locale}", "")).strip()
            item[f"body_{locale}"] = str(form.get(f"body_{locale}", "")).strip()
            if not item[f"label_{locale}"] or len(item[f"label_{locale}"]) > 80:
                raise HTTPException(status_code=400, detail="Shablon nomi uch tilda 1-80 belgi bo‘lsin.")
            if not item[f"body_{locale}"] or len(item[f"body_{locale}"]) > 4000:
                raise HTTPException(status_code=400, detail="Shablon matni uch tilda 1-4000 belgi bo‘lsin.")
        config["response_templates"].append(item)
        await _save_config(db, admin, config, "Javob shabloni qo‘shildi")
        return RedirectResponse("/admin/bot?outcome=draft", status_code=303)

    @router.post("/admin/bot/response-templates/{item_id}/delete")
    async def delete_response_template(
        item_id: str,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        config = await _edit_config(db)
        filtered = [item for item in config["response_templates"] if item["id"] != item_id]
        if len(filtered) == len(config["response_templates"]):
            raise HTTPException(status_code=404, detail="Javob shabloni topilmadi.")
        config["response_templates"] = filtered
        await _save_config(db, admin, config, "Javob shabloni o‘chirildi")
        return RedirectResponse("/admin/bot?outcome=draft", status_code=303)

    @router.post("/admin/bot/revisions/{revision_id}/preview")
    async def preview_revision(
        revision_id: int,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        try:
            await mark_bot_revision_previewed(db, revision_id, admin.id)
            await db.commit()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(f"/admin/bot?previewed={revision_id}", status_code=303)

    @router.post("/admin/bot/revisions/{revision_id}/publish")
    async def publish_revision(
        revision_id: int,
        request: Request,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        form = await request.form()
        if form.get("confirm_publish") != "yes":
            raise HTTPException(status_code=400, detail="Nashrni alohida tasdiqlash kerak.")
        try:
            await publish_bot_revision(db, revision_id, admin.id)
            await db.commit()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse("/admin/bot?outcome=published", status_code=303)

    @router.post("/admin/bot/revisions/{revision_id}/rollback")
    async def rollback_revision(
        revision_id: int,
        request: Request,
        admin=Depends(admin_dependency),
        db: AsyncSession = Depends(get_session),
        _=Depends(csrf_dependency),
    ):
        form = await request.form()
        if form.get("confirm_rollback") != "yes":
            raise HTTPException(status_code=400, detail="Qaytarishni alohida tasdiqlash kerak.")
        try:
            await rollback_bot_revision(db, revision_id, admin.id)
            await db.commit()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse("/admin/bot?outcome=published", status_code=303)

    return router
