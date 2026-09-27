from copy import deepcopy

import pytest
from sqlalchemy import select

from app.bot_config import (
    DEFAULT_BOT_CONFIG,
    deadline_days_for,
    mark_bot_revision_previewed,
    normalize_bot_config,
    publish_bot_revision,
    read_bot_config,
    rollback_bot_revision,
    save_bot_draft,
)
from app.models import AdminUser, AuditEvent, AdminRole


def test_deadline_uses_category_sla_then_published_type_policy():
    config = deepcopy(DEFAULT_BOT_CONFIG)
    config["deadline_policy"].update(ariza_days=18, shikoyat_days=22, taklif_days=31)

    assert deadline_days_for(config, "ariza", category_sla_days=7) == 7
    assert deadline_days_for(config, "shikoyat") == 22
    assert deadline_days_for(config, "unknown-type") == 18
    assert deadline_days_for(config, "ariza", category_sla_days=True) == 18
    assert deadline_days_for(config, "ariza", category_sla_days=500) == 18


def test_long_telegram_text_is_split_without_losing_emoji():
    from app.bot import split_telegram_text

    original = "🙂" * 2100 + "[long]"
    chunks = split_telegram_text(original)
    assert len(chunks) > 1
    assert "".join(chunks) == original
    assert all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in chunks)


@pytest.mark.asyncio
async def test_bot_configuration_requires_preview_publish_and_supports_rollback(db_session):
    admin = AdminUser(
        username="config-version-test",
        password_hash="test-only",
        full_name="Config Test",
        role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.flush()

    first_value = deepcopy(DEFAULT_BOT_CONFIG)
    first_value["welcome_message_uz"] = "Birinchi tasdiqlangan matn"
    first = await save_bot_draft(db_session, first_value, admin.id, "Birinchi nashr")
    with pytest.raises(ValueError, match="qoralamani"):
        await publish_bot_revision(db_session, first.id, admin.id)
    await mark_bot_revision_previewed(db_session, first.id, admin.id)
    await publish_bot_revision(db_session, first.id, admin.id)
    await db_session.commit()
    assert (await read_bot_config(db_session))["welcome_message_uz"] == "Birinchi tasdiqlangan matn"

    second_value = deepcopy(DEFAULT_BOT_CONFIG)
    second_value["welcome_message_uz"] = "Ikkinchi matn"
    second = await save_bot_draft(db_session, second_value, admin.id, "Ikkinchi nashr")
    await mark_bot_revision_previewed(db_session, second.id, admin.id)
    await publish_bot_revision(db_session, second.id, admin.id)
    await db_session.commit()
    assert (await read_bot_config(db_session))["welcome_message_uz"] == "Ikkinchi matn"

    await rollback_bot_revision(db_session, first.id, admin.id)
    await db_session.commit()
    assert (await read_bot_config(db_session))["welcome_message_uz"] == "Birinchi tasdiqlangan matn"

    actions = (await db_session.execute(
        select(AuditEvent.action).where(AuditEvent.entity_type == "bot_config")
    )).scalars().all()
    assert "bot_config_previewed" in actions
    assert actions.count("bot_config_published") == 2
    assert "bot_config_rolled_back" in actions


@pytest.mark.asyncio
async def test_published_settings_refresh_into_bot_runtime_without_restart(db_session, monkeypatch):
    from contextlib import asynccontextmanager

    import app.bot_config as bot_config_module

    admin = AdminUser(
        username="runtime-refresh-test",
        password_hash="test-only",
        full_name="Runtime Refresh Test",
        role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.flush()

    value = deepcopy(DEFAULT_BOT_CONFIG)
    value["welcome_message_uz"] = "Sinov nashridan keyingi salomlashuv"
    revision = await save_bot_draft(db_session, value, admin.id, "Runtime refresh test")
    await mark_bot_revision_previewed(db_session, revision.id, admin.id)
    await publish_bot_revision(db_session, revision.id, admin.id)
    await db_session.commit()

    @asynccontextmanager
    async def test_session():
        yield db_session

    monkeypatch.setattr(bot_config_module, "get_session_factory", lambda: test_session)
    monkeypatch.setattr(bot_config_module, "_runtime_config", deepcopy(DEFAULT_BOT_CONFIG))
    assert bot_config_module.get_runtime_bot_config()["welcome_message_uz"] == ""

    await bot_config_module.refresh_runtime_bot_config()

    assert bot_config_module.get_runtime_bot_config()["welcome_message_uz"] == value["welcome_message_uz"]


def test_sensitive_identity_fields_stay_disabled_even_if_submitted_in_configuration():
    unsafe = deepcopy(DEFAULT_BOT_CONFIG)
    unsafe["form_fields"]["passport_data"] = {"enabled": True, "required": True}
    unsafe["form_fields"]["birth_date"] = {"enabled": True, "required": True}
    unsafe["translation_overrides"] = {
        "consent_text": {"uz": "unapproved", "uz_cyrillic": "unapproved", "ru": "unapproved"},
        "main_menu": {"uz": "custom", "uz_cyrillic": "custom", "ru": "custom"},
        "unknown_key": {"uz": "ignored", "uz_cyrillic": "ignored", "ru": "ignored"},
    }
    safe = normalize_bot_config(unsafe)
    assert safe["form_fields"]["passport_data"] == {"enabled": False, "required": False}
    assert safe["form_fields"]["birth_date"] == {"enabled": False, "required": False}
    assert "consent_text" not in safe["translation_overrides"]
    assert "unknown_key" not in safe["translation_overrides"]
    assert safe["translation_overrides"]["main_menu"]["uz"] == "custom"


def test_mfy_and_street_address_cannot_be_disabled_or_optional():
    unsafe = deepcopy(DEFAULT_BOT_CONFIG)
    unsafe["form_fields"]["mfy"] = {"enabled": False, "required": False}
    unsafe["form_fields"]["address"] = {"enabled": False, "required": False}

    safe = normalize_bot_config(unsafe)

    assert safe["form_fields"]["mfy"] == {"enabled": True, "required": True}
    assert safe["form_fields"]["address"] == {"enabled": True, "required": True}


@pytest.mark.asyncio
async def test_blank_address_and_legacy_skip_do_not_advance_complaint(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.bot as bot_module

    save_draft = AsyncMock()
    monkeypatch.setattr(bot_module, "save_draft", save_draft)
    context = SimpleNamespace(user_data={"lang": "uz"})
    message = SimpleNamespace(text="   ", reply_text=AsyncMock())
    text_update = SimpleNamespace(
        message=message,
        callback_query=None,
        effective_user=SimpleNamespace(id=10),
    )

    assert await bot_module.handle_address(text_update, context) == bot_module.ENTER_ADDRESS
    message.reply_text.assert_awaited_once()
    save_draft.assert_not_awaited()

    query = SimpleNamespace(data="address_skip", answer=AsyncMock())
    callback_update = SimpleNamespace(callback_query=query, message=None)
    assert await bot_module.handle_address(callback_update, context) == bot_module.ENTER_ADDRESS
    query.answer.assert_awaited_once_with(
        bot_module.t("invalid_address_detail", "uz"), show_alert=True
    )
    save_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_mfy_skip_is_rejected_once():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.bot as bot_module

    query = SimpleNamespace(data="mfy_skip", answer=AsyncMock())
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={"lang": "uz"})

    assert await bot_module.handle_mfy_selection(update, context) == bot_module.SELECT_MFY
    query.answer.assert_awaited_once_with("Mahallani tanlash shart.", show_alert=True)


@pytest.mark.asyncio
async def test_mfy_callback_from_deactivated_old_catalog_is_replaced(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.bot as bot_module

    @asynccontextmanager
    async def fake_session():
        yield object()

    monkeypatch.setattr(bot_module, "get_session_factory", lambda: lambda: fake_session())
    monkeypatch.setattr(
        bot_module.CatalogService,
        "get_active_mfy_areas",
        AsyncMock(return_value=[SimpleNamespace(id=58, name_uz="Yangi MFY", name_uz_cyrillic="Янги МФЙ", name_ru="МСГ Янги")]),
    )
    save_draft = AsyncMock()
    monkeypatch.setattr(bot_module, "save_draft", save_draft)
    query = SimpleNamespace(
        data="mfy_1",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        from_user=SimpleNamespace(id=10),
    )
    update = SimpleNamespace(callback_query=query, effective_message=query.message if hasattr(query, "message") else None)
    context = SimpleNamespace(user_data={"lang": "uz"})

    assert await bot_module.handle_mfy_selection(update, context) == bot_module.SELECT_MFY
    assert "mfy_area_id" not in context.user_data
    query.edit_message_text.assert_awaited()
    save_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_sensitive_identity_configuration_is_rejected_before_a_draft_is_saved(db_session):
    admin = AdminUser(
        username="config-sensitive-test",
        password_hash="test-only",
        full_name="Config Test",
        role=AdminRole.SUPER_ADMIN.value,
    )
    db_session.add(admin)
    await db_session.flush()
    unsafe = deepcopy(DEFAULT_BOT_CONFIG)
    unsafe["form_fields"]["passport_data"] = {"enabled": True, "required": True}
    with pytest.raises(ValueError, match="Pasport"):
        await save_bot_draft(db_session, unsafe, admin.id)


def test_admin_translation_overrides_resolve_all_complaint_locales(monkeypatch):
    import app.bot as bot_module
    config = {
        "translation_overrides": {
            "main_menu": {
                "uz": "Uzbek Latin menu",
                "uz_cyrillic": "Uzbek Cyrillic menu",
                "ru": "Russian menu",
            },
            "duplicate_warning": {
                "uz": "Case {tracking_id}",
                "uz_cyrillic": "Ish {tracking_id}",
                "ru": "Дело {tracking_id}",
            },
        }
    }
    monkeypatch.setattr(bot_module, "get_runtime_bot_config", lambda: config)
    assert bot_module.t("main_menu", "uz") == "Uzbek Latin menu"
    assert bot_module.t("main_menu", "uz_cyrillic") == "Uzbek Cyrillic menu"
    assert bot_module.t("main_menu", "ru") == "Russian menu"
    assert bot_module.t("duplicate_warning", "uz_cyrillic", tracking_id="FTMT-TEST") == "Ish FTMT-TEST"


def test_citizen_catalog_prompts_keep_internal_demo_status_out_of_copy():
    from app.i18n import TEXTS

    for key in ("select_mfy", "select_category"):
        for locale in ("uz", "uz_cyrillic", "ru"):
            copy = TEXTS[key].get(locale, "")
            assert "demo" not in copy.casefold()
            assert "демо" not in copy.casefold()

    assert TEXTS["select_category"]["uz_cyrillic"]


def test_welcome_after_language_selection_points_to_menu():
    from app.i18n import t

    assert "interfeys tilini tanlang" not in t("welcome", "uz").casefold()
    assert "выберите язык интерфейса" not in t("welcome", "ru").casefold()
    assert "menyudan" in t("welcome", "uz").casefold()
    assert "меню" in t("welcome", "ru").casefold()


def test_published_menu_buttons_are_not_treated_as_form_text(monkeypatch):
    from datetime import datetime, timezone
    from telegram import Chat, Message, Update

    import app.bot as bot_module

    config = deepcopy(DEFAULT_BOT_CONFIG)
    config["translation_overrides"] = {
        "btn_new_complaint": {
            "uz": "Yangi murojaat",
            "uz_cyrillic": "Янги мурожаат",
            "ru": "Новое обращение",
        }
    }
    monkeypatch.setattr(bot_module, "get_runtime_bot_config", lambda: config)

    for locale, label in config["translation_overrides"]["btn_new_complaint"].items():
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=1, type="private"),
            text=label,
        )
        assert bot_module.configured_menu_action(label) == "btn_new_complaint", locale
        telegram_update = Update(update_id=1, message=message)
        assert not bot_module.FORM_TEXT_FILTER.check_update(telegram_update), locale

    ordinary_text = Message(
        message_id=2,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        text="Ko‘chamizda ichimlik suvi yo‘q",
    )
    assert bot_module.FORM_TEXT_FILTER.check_update(
        Update(update_id=2, message=ordinary_text)
    )


@pytest.mark.asyncio
async def test_submit_reopens_required_address_step_for_invalid_saved_value(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.bot as bot_module

    monkeypatch.setattr(
        bot_module, "get_runtime_bot_config", lambda: deepcopy(DEFAULT_BOT_CONFIG)
    )
    query = SimpleNamespace(
        data="confirm_send",
        from_user=SimpleNamespace(id=42),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={
        "lang": "uz",
        "complaint_language": "uz_latin",
        "complaint_type": "ariza",
        "full_name": "Ali Valiyev",
        "phone_number": "+998901234567",
        "mfy_area_id": 1,
        "address_detail": "12 uy",
        "category_id": 1,
        "title": "Suv ta’minoti",
        "complaint_text": "Mahallamizda ichimlik suvi ta’minotida uzilish bor.",
        "attachments": [],
    })

    state = await bot_module.submit_complaint(update, context)

    assert state == bot_module.ENTER_ADDRESS
    query.edit_message_text.assert_awaited_once()


def test_bot_application_builds_with_declared_conversation_states(monkeypatch):
    from types import SimpleNamespace

    import app.bot as bot_module

    monkeypatch.setattr(
        bot_module,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"),
    )

    application = bot_module.create_bot_application()

    assert application.bot is not None
    assert len(application.handlers[0]) >= 5


@pytest.mark.asyncio
async def test_complaint_start_reaches_language_choice_without_live_telegram_or_data(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.bot as bot_module

    @asynccontextmanager
    async def fake_session():
        yield object()

    monkeypatch.setattr(bot_module, "get_user_lang", AsyncMock(return_value="uz"))
    monkeypatch.setattr(
        bot_module, "get_runtime_bot_config", lambda: deepcopy(DEFAULT_BOT_CONFIG)
    )
    monkeypatch.setattr(bot_module, "get_session_factory", lambda: fake_session)
    monkeypatch.setattr(
        bot_module.UserService,
        "get_or_create_user",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(bot_module.UserService, "has_valid_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(
        bot_module.RateLimitService, "check_rate_limit", AsyncMock(return_value=(True, 0))
    )
    monkeypatch.setattr(bot_module.DraftService, "get_draft", AsyncMock(return_value=None))

    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123456789),
        message=message,
        callback_query=None,
    )
    context = SimpleNamespace(user_data={})

    state = await bot_module.complaint_start(update, context)

    assert state == bot_module.COMPLAINT_LANG
    buttons = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].callback_data for row in buttons] == [
        "clang_uz_latin",
        "clang_uz_cyrillic",
        "clang_ru",
    ]


@pytest.mark.asyncio
async def test_complete_citizen_submission_flow_persists_address_and_tracking_id(db_session, monkeypatch):
    """Exercise each citizen bot step against an isolated DB and fake Telegram updates."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.bot as bot_module
    from app.models import Category, Complaint, ConsentRecord, MFYArea, User

    telegram_id = 987654321
    user = User(telegram_id=telegram_id, interface_language="uz")
    mfy = MFYArea(
        name_uz="Sinov MFY",
        name_uz_cyrillic="Синов МФЙ",
        name_ru="Тест МСГ",
        is_active=True,
    )
    category = Category(
        code="synthetic-water-test",
        name_uz="Sinov yo‘nalishi",
        name_uz_cyrillic="Синов йўналиши",
        name_ru="Тестовое направление",
        is_active=True,
    )
    db_session.add_all([user, mfy, category])
    await db_session.flush()
    db_session.add(ConsentRecord(
        user_id=user.id,
        consent_version=bot_module.CONSENT_VERSION,
        consent_text_hash="synthetic-test-consent-hash",
        accepted=True,
    ))
    await db_session.commit()

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(bot_module, "get_session_factory", lambda: fake_session)
    monkeypatch.setattr(
        bot_module, "get_runtime_bot_config", lambda: deepcopy(DEFAULT_BOT_CONFIG)
    )

    citizen = SimpleNamespace(id=telegram_id, username="synthetic-test-user")
    context = SimpleNamespace(user_data={})

    def message_update(text=None, contact=None):
        message = SimpleNamespace(
            text=text,
            contact=contact,
            reply_text=AsyncMock(),
            photo=[],
            document=None,
            video=None,
        )
        return SimpleNamespace(
            message=message,
            effective_message=message,
            effective_user=citizen,
            callback_query=None,
        )

    def callback_update(data):
        message = SimpleNamespace(reply_text=AsyncMock())
        query = SimpleNamespace(
            data=data,
            from_user=citizen,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=message,
        )
        return SimpleNamespace(
            callback_query=query,
            effective_user=citizen,
            effective_message=message,
            message=None,
        )

    assert await bot_module.complaint_start(message_update("Murojaat yuborish"), context) == bot_module.COMPLAINT_LANG
    assert await bot_module.handle_complaint_lang(callback_update("clang_uz_latin"), context) == bot_module.COMPLAINT_TYPE
    assert await bot_module.handle_complaint_type(callback_update("ctype_ariza"), context) == bot_module.ENTER_NAME
    assert await bot_module.handle_name(message_update("Ali Valiyev"), context) == bot_module.ENTER_PHONE
    assert await bot_module.handle_phone(
        message_update(contact=SimpleNamespace(phone_number="+998901234567", user_id=telegram_id)),
        context,
    ) == bot_module.SELECT_MFY
    assert await bot_module.handle_mfy_selection(callback_update(f"mfy_{mfy.id}"), context) == bot_module.ENTER_ADDRESS
    assert await bot_module.handle_address(message_update("12, Bog‘bon ko‘chasi"), context) == bot_module.SELECT_CATEGORY
    assert await bot_module.handle_category(callback_update(f"cat_{category.id}"), context) == bot_module.ENTER_TITLE
    assert await bot_module.handle_title(message_update("Ichimlik suvi uzilishi"), context) == bot_module.ENTER_DESCRIPTION
    assert await bot_module.handle_description(message_update("Sinov mahallasida suv ta’minotida uzilish bor."), context) == bot_module.ATTACHMENTS
    assert await bot_module.handle_attachment(callback_update("finish_attach"), context) == bot_module.PREVIEW

    submit_update = callback_update("confirm_send")
    assert await bot_module.submit_complaint(submit_update, context) == bot_module.ConversationHandler.END

    complaint = (await db_session.execute(
        select(Complaint).where(Complaint.user_id == user.id)
    )).scalar_one()
    assert complaint.status == "submitted"
    assert complaint.mfy_area_id == mfy.id
    assert complaint.address_detail == "12, Bog‘bon ko‘chasi"
    assert complaint.tracking_id.startswith("FTMT-")
    assert complaint.tracking_id in submit_update.callback_query.edit_message_text.await_args.args[0]


@pytest.mark.asyncio
async def test_start_language_consent_and_main_menu_flow(db_session, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.bot as bot_module
    from app.models import ConsentRecord, User

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(bot_module, "get_session_factory", lambda: fake_session)
    monkeypatch.setattr(
        bot_module, "get_runtime_bot_config", lambda: deepcopy(DEFAULT_BOT_CONFIG)
    )
    citizen = SimpleNamespace(id=987654322, username="synthetic-start-user")
    context = SimpleNamespace(user_data={})
    start_message = SimpleNamespace(reply_text=AsyncMock())
    start_update = SimpleNamespace(message=start_message, effective_user=citizen)

    assert await bot_module.start_command(start_update, context) == bot_module.SELECTING_LANG
    start_keyboard = start_message.reply_text.await_args.kwargs["reply_markup"]
    assert [button.callback_data for button in start_keyboard.inline_keyboard[0]] == [
        "set_lang_uz",
        "set_lang_ru",
    ]

    welcome_message = SimpleNamespace(reply_text=AsyncMock())
    language_query = SimpleNamespace(
        data="set_lang_uz",
        from_user=citizen,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=welcome_message,
    )
    language_update = SimpleNamespace(callback_query=language_query)
    assert await bot_module.handle_language_selection(language_update, context) == bot_module.CONSENT
    consent_keyboard = language_query.edit_message_text.await_args.kwargs["reply_markup"]
    assert {button.callback_data for button in consent_keyboard.inline_keyboard[0]} == {
        "consent_accept",
        "consent_decline",
    }

    consent_query = SimpleNamespace(
        data="consent_accept",
        from_user=citizen,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=welcome_message,
    )
    consent_update = SimpleNamespace(callback_query=consent_query)
    assert await bot_module.handle_consent(consent_update, context) == bot_module.ConversationHandler.END
    assert welcome_message.reply_text.await_count == 1
    assert welcome_message.reply_text.await_args.kwargs["reply_markup"].keyboard
    assert (await db_session.execute(
        select(ConsentRecord).join(User).where(User.telegram_id == citizen.id)
    )).scalar_one().accepted is True


def test_bot_api_url_is_redacted_from_logs():
    import logging

    from app.logging_config import PIIRedactingFormatter

    record = logging.LogRecord(
        "telegram", logging.ERROR, __file__, 0,
        "Request failed: https://api.telegram.org/botnot-a-real-token/sendMessage",
        (), None,
    )
    rendered = PIIRedactingFormatter("%(message)s").format(record)
    assert "bot[TOKEN_REDACTED]/sendMessage" in rendered
    assert "botnot-a-real-token" not in rendered
