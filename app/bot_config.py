"""Validated, versioned citizen-facing bot configuration.

Draft snapshots never affect the bot. Only an explicitly previewed and published
revision becomes active; the separate bot process refreshes that pointer at runtime.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.change_models import BotConfigRevision
from app.config import get_settings
from app.database import get_session_factory
from app.models import AuditEvent, SystemConfig
from app.i18n import TEXTS

CONFIG_KEY = "bot.public_settings"  # Legacy key retained for a safe upgrade path.
ACTIVE_VERSION_KEY = "bot.active_revision"
LOCALES = ("uz", "uz_cyrillic", "ru")
SENSITIVE_FIELDS = ("passport_data", "birth_date")

DEFAULT_BOT_CONFIG: dict[str, Any] = {
    "maintenance_enabled": False,
    "maintenance_message_uz": "Yangi murojaat qabul qilish vaqtincha to‘xtatilgan.",
    "maintenance_message_uz_cyrillic": "Янги мурожаат қабул қилиш вақтинча тўхтатилган.",
    "maintenance_message_ru": "Приём новых обращений временно приостановлен.",
    "welcome_message_uz": "",
    "welcome_message_uz_cyrillic": "",
    "welcome_message_ru": "",
    "help_message_uz": "",
    "help_message_uz_cyrillic": "",
    "help_message_ru": "",
    "announcement_enabled": False,
    "announcement_uz": "",
    "announcement_uz_cyrillic": "",
    "announcement_ru": "",
    "working_hours_uz": "",
    "working_hours_uz_cyrillic": "",
    "working_hours_ru": "",
    "contact_info_uz": "",
    "contact_info_uz_cyrillic": "",
    "contact_info_ru": "",
    "deadline_reminder_message_uz": "Murojaat {tracking_id} bo‘yicha muddat {deadline} kuni yakunlanadi.",
    "deadline_reminder_message_uz_cyrillic": "Мурожаат {tracking_id} бўйича муддат {deadline} куни якунланади.",
    "deadline_reminder_message_ru": "Срок рассмотрения обращения {tracking_id} истекает {deadline}.",
    "escalation_message_uz": "Murojaat {tracking_id} belgilangan muddatdan oshdi.",
    "escalation_message_uz_cyrillic": "Мурожаат {tracking_id} белгиланган муддатдан ошди.",
    "escalation_message_ru": "Срок рассмотрения обращения {tracking_id} истёк.",
    "confirmation_reminder_message_uz": "📬 Murojaat {tracking_id} bo‘yicha javob berildi. Masala hal bo‘lgan bo‘lsa tasdiqlang, aks holda qayta ko‘rib chiqishni so‘rang.",
    "confirmation_reminder_message_uz_cyrillic": "📬 Мурожаат {tracking_id} бўйича жавоб берилди. Масала ҳал бўлган бўлса тасдиқланг, акс ҳолда қайта кўриб чиқишни сўранг.",
    "confirmation_reminder_message_ru": "📬 По обращению {tracking_id} предоставлен ответ. Подтвердите решение или попросите рассмотреть вопрос повторно.",
    "daily_limit": 3,
    "features": {"submissions": True, "my_complaints": True, "help": True},
    "form_fields": {
        "full_name": {"enabled": True, "required": True},
        "phone": {"enabled": True, "required": True},
        "mfy": {"enabled": True, "required": True},
        "address": {"enabled": True, "required": True},
        "title": {"enabled": True, "required": True},
        "description": {"enabled": True, "required": True},
        "attachments": {"enabled": True, "required": False},
        "passport_data": {"enabled": True, "required": True},
        "birth_date": {"enabled": True, "required": True},
    },
    "field_hints": {
        field: {locale: "" for locale in LOCALES}
        for field in ("full_name", "phone", "passport_data", "birth_date", "mfy", "address", "title", "description", "attachments")
    },
    "deadline_policy": {
        "ariza_days": 15,
        "shikoyat_days": 15,
        "taklif_days": 30,
        "reminder_days_before": 3,
        "escalate_after_days": 3,
        "confirmation_reminder_hours": 48,
    },
    "quick_answers": [],
    "response_templates": [],
    "translation_overrides": {},
}


def _clean_text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def normalize_bot_config(value: Any) -> dict[str, Any]:
    """Return a bounded, known-key configuration; ignore arbitrary data."""
    result = deepcopy(DEFAULT_BOT_CONFIG)
    if not isinstance(value, dict):
        return result

    result["maintenance_enabled"] = value.get("maintenance_enabled") is True
    result["announcement_enabled"] = value.get("announcement_enabled") is True
    text_keys = [key for key in result if key.endswith(tuple(f"_{lang}" for lang in LOCALES))]
    for key in text_keys:
        if key in value:
            result[key] = _clean_text(value[key], 4000)

    limit = value.get("daily_limit")
    if type(limit) is int and 1 <= limit <= 20:
        result["daily_limit"] = limit

    features = value.get("features")
    if isinstance(features, dict):
        for key in result["features"]:
            if type(features.get(key)) is bool:
                result["features"][key] = features[key]

    fields = value.get("form_fields")
    if isinstance(fields, dict):
        for name, defaults in result["form_fields"].items():
            candidate = fields.get(name)
            if not isinstance(candidate, dict):
                continue
            if name in SENSITIVE_FIELDS:
                # Identity checks are mandatory even for previously published configurations.
                result["form_fields"][name] = {"enabled": True, "required": True}
                continue
            enabled = candidate.get("enabled")
            if name in {"full_name", "phone", "title", "description"}:
                result["form_fields"][name] = {"enabled": True, "required": True}
                continue
            if name in {"mfy", "address"}:
                # Residence area and street address are required to route a case.
                result["form_fields"][name] = {"enabled": True, "required": True}
                continue
            if type(enabled) is bool:
                required = candidate.get("required") is True and enabled
                result["form_fields"][name] = {"enabled": enabled, "required": required}

    hints = value.get("field_hints")
    if isinstance(hints, dict):
        for field, translations in result["field_hints"].items():
            candidate = hints.get(field)
            if isinstance(candidate, dict):
                result["field_hints"][field] = {
                    locale: _clean_text(candidate.get(locale), 500) for locale in LOCALES
                }

    policy = value.get("deadline_policy")
    if isinstance(policy, dict):
        for key, low, high in (
            ("ariza_days", 1, 365),
            ("shikoyat_days", 1, 365),
            ("taklif_days", 1, 365),
            ("reminder_days_before", 1, 30),
            ("escalate_after_days", 1, 365),
            ("confirmation_reminder_hours", 1, 336),
        ):
            candidate = policy.get(key)
            if type(candidate) is int and low <= candidate <= high:
                result["deadline_policy"][key] = candidate

    overrides = value.get("translation_overrides")
    if isinstance(overrides, dict):
        protected = {key for key in TEXTS if key.startswith("consent_")}
        result["translation_overrides"] = {
            key: {locale: _clean_text(translations.get(locale), 3500) for locale in LOCALES}
            for key, translations in overrides.items()
            if key in TEXTS and key not in protected and isinstance(translations, dict)
            and any(_clean_text(translations.get(locale), 3500) for locale in LOCALES)
        }

    for list_key, label_limit, text_limit in (
        ("quick_answers", 32, 3500),
        ("response_templates", 80, 4000),
    ):
        entries = value.get(list_key)
        if not isinstance(entries, list):
            continue
        cleaned = []
        for item in entries[:20]:
            if not isinstance(item, dict):
                continue
            clean = {"id": _clean_text(item.get("id"), 64), "enabled": item.get("enabled") is not False}
            label_key = "label" if list_key == "response_templates" else "label"
            for locale in LOCALES:
                clean[f"{label_key}_{locale}"] = _clean_text(item.get(f"{label_key}_{locale}"), label_limit)
                text_key = "body" if list_key == "response_templates" else "response"
                clean[f"{text_key}_{locale}"] = _clean_text(item.get(f"{text_key}_{locale}"), text_limit)
            if not clean["id"] or any(not clean[f"label_{locale}"] for locale in LOCALES):
                continue
            if any(not clean[f"{text_key}_{locale}"] for locale in LOCALES):
                continue
            cleaned.append(clean)
        result[list_key] = cleaned
    return result


def locale_code(language: str | None) -> str:
    if language == "uz_cyrillic":
        return "uz_cyrillic"
    if language in {"ru", "uz"}:
        return language
    return "uz"


def deadline_days_for(
    config: dict[str, Any], complaint_type: str, category_sla_days: int | None = None
) -> int:
    """Resolve an approved category SLA first, then the published type policy."""
    if type(category_sla_days) is int and 1 <= category_sla_days <= 365:
        return category_sla_days
    policy = normalize_bot_config(config)["deadline_policy"]
    policy_key = {
        "ariza": "ariza_days",
        "shikoyat": "shikoyat_days",
        "taklif": "taklif_days",
    }.get(complaint_type, "ariza_days")
    return policy[policy_key]


def localized_config(config: dict[str, Any], prefix: str, language: str | None) -> str:
    locale = locale_code(language)
    return (
        config.get(f"{prefix}_{locale}")
        or config.get(f"{prefix}_uz")
        or config.get(f"{prefix}_ru")
        or ""
    )


def _decode_config(raw: str | None) -> dict[str, Any]:
    try:
        return normalize_bot_config(json.loads(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return deepcopy(DEFAULT_BOT_CONFIG)


async def _read_config_row(session: AsyncSession, key: str) -> SystemConfig | None:
    return (await session.execute(
        select(SystemConfig).where(SystemConfig.key == key)
    )).scalar_one_or_none()


async def active_revision_id(session: AsyncSession) -> int | None:
    pointer = await _read_config_row(session, ACTIVE_VERSION_KEY)
    try:
        return int(pointer.value) if pointer and pointer.value.isdigit() else None
    except (AttributeError, ValueError):
        return None


async def read_bot_config(session: AsyncSession) -> dict[str, Any]:
    """Read the published revision, falling back to legacy config/defaults."""
    revision_id = await active_revision_id(session)
    if revision_id is not None:
        revision = await session.get(BotConfigRevision, revision_id)
        if revision and revision.state == "published":
            return _decode_config(revision.config_json)

    legacy = await _read_config_row(session, CONFIG_KEY)
    if legacy:
        return _decode_config(legacy.value)
    defaults = deepcopy(DEFAULT_BOT_CONFIG)
    defaults["daily_limit"] = min(max(int(get_settings().max_complaints_per_day), 1), 20)
    return defaults


async def read_admin_bot_state(session: AsyncSession) -> dict[str, Any]:
    active_id = await active_revision_id(session)
    active_config = await read_bot_config(session)
    draft = (await session.execute(
        select(BotConfigRevision)
        .where(BotConfigRevision.state == "draft")
        .order_by(BotConfigRevision.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    revisions = (await session.execute(
        select(BotConfigRevision).order_by(BotConfigRevision.id.desc()).limit(12)
    )).scalars().all()
    return {
        "active_revision_id": active_id,
        "active_config": active_config,
        "draft": draft,
        "draft_config": _decode_config(draft.config_json) if draft else active_config,
        "revisions": revisions,
    }


def _audit(session: AsyncSession, action: str, actor_id: int, revision_id: int, **details: Any) -> None:
    session.add(AuditEvent(
        action=action,
        entity_type="bot_config",
        entity_id=str(revision_id),
        actor_type="admin",
        actor_id=str(actor_id),
        new_value=json.dumps(details, ensure_ascii=False),
    ))


async def save_bot_draft(
    session: AsyncSession, value: dict[str, Any], updated_by_id: int, note: str | None = None
) -> BotConfigRevision:
    """Create a new immutable draft snapshot and supersede any older unapproved draft."""
    safe_value = normalize_bot_config(value)
    await session.execute(
        update(BotConfigRevision)
        .where(BotConfigRevision.state == "draft")
        .values(state="superseded")
    )
    revision = BotConfigRevision(
        config_json=json.dumps(safe_value, ensure_ascii=False),
        state="draft",
        note=_clean_text(note, 500) or None,
        created_by_id=updated_by_id,
    )
    session.add(revision)
    await session.flush()
    _audit(session, "bot_config_draft_saved", updated_by_id, revision.id, note=revision.note or "")
    return revision


async def mark_bot_revision_previewed(
    session: AsyncSession, revision_id: int, admin_id: int
) -> BotConfigRevision:
    revision = await session.get(BotConfigRevision, revision_id)
    if revision is None or revision.state != "draft":
        raise ValueError("Ko‘rib chiqiladigan qoralama topilmadi.")
    revision.previewed_by_id = admin_id
    revision.previewed_at = datetime.now(timezone.utc)
    _audit(session, "bot_config_previewed", admin_id, revision.id)
    await session.flush()
    return revision


async def publish_bot_revision(
    session: AsyncSession, revision_id: int, admin_id: int
) -> BotConfigRevision:
    revision = await session.get(BotConfigRevision, revision_id)
    if revision is None or revision.state != "draft":
        raise ValueError("E’lon qilinadigan qoralama topilmadi.")
    if revision.previewed_at is None:
        raise ValueError("Avval qoralamani ko‘rib chiqing.")
    config = _decode_config(revision.config_json)
    previous_id = await active_revision_id(session)
    pointer = await _read_config_row(session, ACTIVE_VERSION_KEY)
    if pointer is None:
        pointer = SystemConfig(
            key=ACTIVE_VERSION_KEY,
            value=str(revision.id),
            description="Published bot configuration revision",
            updated_by_id=admin_id,
        )
        session.add(pointer)
    else:
        pointer.value = str(revision.id)
        pointer.updated_by_id = admin_id
    revision.state = "published"
    revision.published_by_id = admin_id
    revision.published_at = datetime.now(timezone.utc)
    await session.execute(
        update(BotConfigRevision)
        .where(BotConfigRevision.state == "draft", BotConfigRevision.id != revision.id)
        .values(state="superseded")
    )
    _audit(
        session,
        "bot_config_published",
        admin_id,
        revision.id,
        previous_revision_id=previous_id,
        active_revision_id=revision.id,
        field_count=len(config),
    )
    await session.flush()
    return revision


async def rollback_bot_revision(
    session: AsyncSession, revision_id: int, admin_id: int
) -> BotConfigRevision:
    revision = await session.get(BotConfigRevision, revision_id)
    if revision is None or revision.state != "published":
        raise ValueError("Faqat oldin e’lon qilingan versiyaga qaytish mumkin.")
    previous_id = await active_revision_id(session)
    pointer = await _read_config_row(session, ACTIVE_VERSION_KEY)
    if pointer is None:
        pointer = SystemConfig(
            key=ACTIVE_VERSION_KEY,
            value=str(revision.id),
            description="Published bot configuration revision",
            updated_by_id=admin_id,
        )
        session.add(pointer)
    else:
        pointer.value = str(revision.id)
        pointer.updated_by_id = admin_id
    _audit(
        session,
        "bot_config_rolled_back",
        admin_id,
        revision.id,
        previous_revision_id=previous_id,
        active_revision_id=revision.id,
    )
    await session.flush()
    return revision


_runtime_config = deepcopy(DEFAULT_BOT_CONFIG)


async def refresh_runtime_bot_config() -> None:
    """Reload the active revision for the separate bot process."""
    global _runtime_config
    async with get_session_factory()() as session:
        _runtime_config = await read_bot_config(session)


def get_runtime_bot_config() -> dict[str, Any]:
    """Return a copy so handlers cannot mutate shared configuration."""
    return deepcopy(_runtime_config)
