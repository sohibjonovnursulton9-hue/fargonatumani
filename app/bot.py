"""
Telegram bot for citizen complaint submission.
Uses python-telegram-bot v20+ async ConversationHandler.

All user-facing text comes from app.i18n module.
PII is never logged. Drafts saved at each step.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.config import get_settings
from app.database import get_session_factory
from app.bot_config import (
    deadline_days_for,
    get_runtime_bot_config,
    localized_config,
    refresh_runtime_bot_config,
)
from app.i18n import get_status_label as _base_status_label, get_type_label as _base_type_label, t as _base_t
from app.logging_config import get_logger
from app.models import (
    Complaint,
    ComplaintLanguage,
    ComplaintStatus,
    ComplaintType,
    DeadlineExtension,
    Attachment,
    Notification,
    User,
)
from app.services import (
    CatalogService,
    ComplaintService,
    DraftService,
    RateLimitService,
    UserService,
)

logger = get_logger(__name__)

# ConversationHandler states. Keep the identifiers declared at module scope so
# the bot can be built before it starts polling Telegram.
(
    SELECTING_LANG,
    CONSENT,
    COMPLAINT_LANG,
    COMPLAINT_TYPE,
    ENTER_NAME,
    ENTER_PHONE,
    ENTER_BIRTH_DATE,
    ENTER_PASSPORT,
    SELECT_MFY,
    ENTER_ADDRESS,
    SELECT_CATEGORY,
    ENTER_TITLE,
    ENTER_DESCRIPTION,
    ATTACHMENTS,
    PREVIEW,
    EDITING,
    ADDITIONAL_INFO,
) = range(17)

CONSENT_VERSION = "1.0-draft"
PHONE_REGEX = re.compile(r"^\+998\d{9}$")
MENU_ACTION_KEYS = (
    "btn_new_complaint",
    "btn_my_complaints",
    "btn_help",
    "btn_change_language",
)
MENU_LOCALES = ("uz", "uz_cyrillic", "ru")


def configured_menu_action(text: str | None) -> str | None:
    """Resolve a configured keyboard label to its menu action key."""
    label = (text or "").strip()
    if not label:
        return None
    return next((
        key for key in MENU_ACTION_KEYS
        if any(label == t(key, locale).strip() for locale in MENU_LOCALES)
    ), None)


class ConfiguredButtonFilter(filters.MessageFilter):
    """Match the currently published label for a citizen menu action."""

    def __init__(self, text_key: str) -> None:
        self.text_key = text_key
        super().__init__(name=f"ConfiguredButton:{text_key}")

    def filter(self, message: Message) -> bool:
        text = (message.text or "").strip()
        return bool(text) and any(
            text == t(self.text_key, locale).strip() for locale in MENU_LOCALES
        )


class AnyConfiguredButtonFilter(filters.MessageFilter):
    """Keep menu taps out of free-text form fields while a conversation is active."""

    def __init__(self) -> None:
        super().__init__(name="AnyConfiguredMenuButton")

    def filter(self, message: Message) -> bool:
        text = (message.text or "").strip()
        return bool(text) and any(
            text == t(key, locale).strip()
            for key in MENU_ACTION_KEYS
            for locale in MENU_LOCALES
        ) or bool(text) and any(
            item.get("enabled", True) and text == item.get(f"label_{locale}", "").strip()
            for item in get_runtime_bot_config().get("quick_answers", [])
            for locale in MENU_LOCALES
        )


MENU_BUTTON_FILTERS = {
    key: ConfiguredButtonFilter(key) for key in MENU_ACTION_KEYS
}
FORM_TEXT_FILTER = filters.TEXT & ~filters.COMMAND & ~AnyConfiguredButtonFilter()
PHONE_INPUT_FILTER = FORM_TEXT_FILTER | filters.CONTACT


async def get_user_lang(telegram_id: int) -> str:
    """Read the citizen's saved interface language, creating a default user if needed."""
    async with get_session_factory()() as session:
        user = await UserService.get_or_create_user(session, telegram_id)
        await session.commit()
        return user.interface_language


def _configured_text(key: str, lang: str, **kwargs) -> str | None:
    locale = lang if lang in {"uz", "uz_cyrillic", "ru"} else ("ru" if lang == "ru" else "uz")
    text = get_runtime_bot_config().get("translation_overrides", {}).get(key, {}).get(locale)
    if not text:
        return None
    try:
        return text.format(**kwargs) if kwargs else text
    except (KeyError, IndexError, ValueError):
        logger.warning("Configured bot text has an invalid placeholder", extra={"text_key": key})
        return None


def t(key: str, lang: str, **kwargs) -> str:
    """Resolve an approved admin translation override before the built-in copy."""
    override = _configured_text(key, lang, **kwargs)
    if override is not None:
        return override
    locale = lang if lang in {"uz", "uz_cyrillic", "ru"} else ("ru" if lang == "ru" else "uz")
    return _base_t(key, "ru" if locale == "ru" else "uz", **kwargs)


def get_status_label(status: str, lang: str) -> str:
    return _configured_text(f"status_{status}", lang) or _base_status_label(
        status, "ru" if lang == "ru" else "uz"
    )


def get_type_label(complaint_type: str, lang: str) -> str:
    return _configured_text(f"type_{complaint_type}", lang) or _base_type_label(
        complaint_type, "ru" if lang == "ru" else "uz"
    )


def main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    config = get_runtime_bot_config()
    buttons = []
    if config["features"]["submissions"]:
        buttons.append([KeyboardButton(t("btn_new_complaint", lang))])
    row = []
    if config["features"]["my_complaints"]:
        row.append(KeyboardButton(t("btn_my_complaints", lang)))
    if config["features"]["help"]:
        row.append(KeyboardButton(t("btn_help", lang)))
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton(t("btn_change_language", lang))])
    locale = lang if lang in {"uz", "uz_cyrillic", "ru"} else "uz"
    for answer in config.get("quick_answers", []):
        if answer.get("enabled", True):
            label = answer.get(f"label_{locale}") or answer.get("label_uz")
            if label:
                buttons.append([KeyboardButton(label)])
    if not buttons:
        buttons = [[KeyboardButton(t("btn_help", lang))]]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)


def content_locale(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Use the citizen-selected writing language for complaint-flow copy."""
    language = context.user_data.get("complaint_language")
    return {
        ComplaintLanguage.UZ_LATIN.value: "uz",
        ComplaintLanguage.UZ_CYRILLIC.value: "uz_cyrillic",
        ComplaintLanguage.RU.value: "ru",
    }.get(language, "ru" if context.user_data.get("lang") == "ru" else "uz")


def localized_name(record, context: ContextTypes.DEFAULT_TYPE) -> str:
    locale = content_locale(context)
    if locale == "ru":
        return record.name_ru
    if locale == "uz_cyrillic":
        return record.name_uz_cyrillic
    return record.name_uz


def field_prompt(message: str, field: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Append a bounded configured hint for a citizen-facing form field."""
    config = get_runtime_bot_config()
    hint = config["field_hints"].get(field, {}).get(content_locale(context), "").strip()
    if not hint:
        return message
    for marker in ("\\", "_", "*", chr(96), "[", "]"):
        hint = hint.replace(marker, "\\" + marker)
    return f"{message}\n\nℹ️ {hint}"




def welcome_content(config: dict, lang: str) -> tuple[str, bool]:
    """Compose a localized start message and public operating information."""
    welcome = localized_config(config, "welcome_message", lang)
    custom = bool(welcome)
    if not welcome:
        welcome = _configured_text("welcome", lang) or t("welcome", lang)
        custom = _configured_text("welcome", lang) is not None
    sections = [welcome]
    if config["announcement_enabled"]:
        announcement = localized_config(config, "announcement", lang)
        if announcement:
            sections.append(f"📢 {announcement}")
            custom = True
    for prefix, icon in (("working_hours", "🕒"), ("contact_info", "☎️")):
        text = localized_config(config, prefix, lang)
        if text:
            sections.append(f"{icon} {text}")
            custom = True
    return "\n\n".join(sections), custom


def split_telegram_text(value: str, max_units: int = 3500) -> list[str]:
    """Keep every Telegram message below its UTF-16 length limit."""
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for char in value:
        units = 2 if ord(char) > 0xFFFF else 1
        if current and used + units > max_units:
            chunks.append("".join(current))
            current, used = [], 0
        current.append(char)
        used += units
    if current:
        chunks.append("".join(current))
    return chunks or [""]


async def send_menu_text(message, text: str, lang: str, *, parse_mode=None) -> None:
    chunks = split_telegram_text(text)
    for index, chunk in enumerate(chunks):
        await message.reply_text(
            chunk,
            parse_mode=parse_mode if len(chunks) == 1 else None,
            reply_markup=main_menu_keyboard(lang) if index == len(chunks) - 1 else None,
        )



def format_tashkent_time(dt: datetime | None) -> str:
    """Format UTC datetime as Asia/Tashkent local time string."""
    if dt is None:
        return "—"
    # UTC+5 for Tashkent
    local = dt.replace(tzinfo=timezone.utc).astimezone(
        timezone(offset=__import__("datetime").timedelta(hours=5))
    )
    return local.strftime("%d.%m.%Y %H:%M")


# ============================================================================
# /start Command
# ============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start — show language selection."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="set_lang_uz"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang_ru"),
        ]
    ])

    if update.message:
        await update.message.reply_text(
            "🏛 Farg‘ona tumani murojaatlari tizimiga xush kelibsiz.\n"
            "Interfeys tilini tanlang:\n\n"
            "Добро пожаловать в систему обращений Ферганского района.\n"
            "Выберите язык интерфейса:",
            reply_markup=keyboard,
            parse_mode=None,
        )
    return SELECTING_LANG


async def restart_complaint_with_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Save the current draft, clear stale in-memory fields, and restart language choice."""
    user_id = update.effective_user.id if update.effective_user else None
    if user_id and any(
        context.user_data.get(key)
        for key in ("full_name", "phone_number", "mfy_area_id", "address_detail", "title", "complaint_text", "attachments")
    ):
        await save_draft(user_id, context.user_data)
    context.user_data.clear()
    return await start_command(update, context)


async def handle_language_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Process interface language selection independently from complaint language."""
    query = update.callback_query
    await query.answer()
    lang = query.data.split("_")[-1]

    async with get_session_factory()() as session:
        user = await UserService.get_or_create_user(
            session, query.from_user.id, query.from_user.username, lang
        )
        await UserService.update_language(session, user.id, lang)
        has_consent = await UserService.has_valid_consent(session, user.id, CONSENT_VERSION)

    context.user_data["lang"] = lang
    if not has_consent:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("consent_accept_btn", lang), callback_data="consent_accept"),
            InlineKeyboardButton(t("consent_decline_btn", lang), callback_data="consent_decline"),
        ]])
        await query.edit_message_text(t("consent_text", lang), reply_markup=keyboard, parse_mode="Markdown")
        return CONSENT

    config = get_runtime_bot_config()
    welcome, custom = welcome_content(config, lang)
    await query.edit_message_text(t("language_selected", lang), parse_mode="Markdown")
    await send_menu_text(
        query.message, welcome, lang, parse_mode=None if custom else "Markdown",
    )
    return ConversationHandler.END



async def handle_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Record consent and show the selected interface language."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "uz")
    accepted = query.data == "consent_accept"

    async with get_session_factory()() as session:
        user = await UserService.get_or_create_user(session, query.from_user.id)
        await UserService.record_consent(
            session, user.id, CONSENT_VERSION, t("consent_text", lang), accepted
        )
        await session.commit()

    if not accepted:
        await query.edit_message_text(t("consent_declined", lang), parse_mode="Markdown")
        return ConversationHandler.END

    config = get_runtime_bot_config()
    welcome, custom = welcome_content(config, lang)
    await query.edit_message_text(t("consent_accepted", lang), parse_mode="Markdown")
    await send_menu_text(
        query.message, welcome, lang, parse_mode=None if custom else "Markdown",
    )
    return ConversationHandler.END


# ============================================================================
# Complaint Submission ConversationHandler



async def complaint_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Entry point for complaint submission."""
    lang = await get_user_lang(update.effective_user.id)
    context.user_data.clear()
    context.user_data["lang"] = lang
    context.user_data["attachments"] = []
    bot_config = get_runtime_bot_config()
    if bot_config["maintenance_enabled"] or not bot_config["features"]["submissions"]:
        message = (
            localized_config(bot_config, "maintenance_message", lang)
        ) if bot_config["maintenance_enabled"] else (
            "Yangi murojaat yuborish vaqtincha mavjud emas."
            if lang != "ru" else "Подача новых обращений временно недоступна."
        )
        await update.message.reply_text(message, reply_markup=main_menu_keyboard(lang))
        return ConversationHandler.END

    # Recheck consent here; citizens can enter this flow without using /start again.
    async with get_session_factory()() as session:
        user = await UserService.get_or_create_user(session, update.effective_user.id)
        has_consent = await UserService.has_valid_consent(session, user.id, CONSENT_VERSION)
        allowed, count = await RateLimitService.check_rate_limit(
            session, update.effective_user.id, max_per_day=bot_config["daily_limit"]
        )

    if not has_consent:
        message = (
            "Ariza yuborishdan oldin /start orqali shaxsiy ma’lumotlarni qayta ishlashga rozilik bering."
            if lang != "ru"
            else "Перед подачей обращения подтвердите согласие на обработку персональных данных через /start."
        )
        await update.message.reply_text(message, reply_markup=main_menu_keyboard(lang))
        return ConversationHandler.END

    if not allowed:
        await update.message.reply_text(
            t("rate_limit_exceeded", lang, max=bot_config["daily_limit"]),
            reply_markup=main_menu_keyboard(lang),
        )
        return ConversationHandler.END

    # Check for existing draft
    async with get_session_factory()() as session:
        user = await UserService.get_or_create_user(session, update.effective_user.id)
        draft = await DraftService.get_draft(session, user.id)

    if draft:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    t("btn_continue_draft", lang), callback_data="draft_continue"
                ),
                InlineKeyboardButton(
                    t("btn_new_complaint_discard", lang), callback_data="draft_new"
                ),
            ]
        ])
        await update.message.reply_text(
            t("draft_found", lang),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return COMPLAINT_LANG  # We handle draft choice in this state

    # Show complaint language selection
    return await show_complaint_lang_selection(update, context)


async def show_complaint_lang_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Show complaint language selection."""
    lang = context.user_data.get("lang", "uz")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_lang_uz_latin", lang), callback_data="clang_uz_latin")],
        [InlineKeyboardButton(t("btn_lang_uz_cyrillic", lang), callback_data="clang_uz_cyrillic")],
        [InlineKeyboardButton(t("btn_lang_ru", lang), callback_data="clang_ru")],
    ])

    msg = t("select_complaint_language", lang)
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    return COMPLAINT_LANG


async def handle_complaint_lang(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle complaint language or draft choice."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "uz")

    if query.data == "draft_continue":
        async with get_session_factory()() as session:
            user = await UserService.get_or_create_user(session, query.from_user.id)
            draft = await DraftService.get_draft(session, user.id)
        if not draft:
            await query.edit_message_text(
                "Saqlangan qoralama topilmadi. Murojaatni yangidan boshlaymiz."
                if lang != "ru" else "Черновик не найден. Начнём обращение заново."
            )
            return await show_complaint_lang_selection(update, context)
        try:
            draft_data = json.loads(draft.draft_data)
        except (json.JSONDecodeError, TypeError):
            draft_data = {}
        context.user_data.clear()
        if isinstance(draft_data, dict):
            context.user_data.update(draft_data)
        context.user_data["lang"] = lang
        return await resume_saved_draft(update, context)

    if query.data == "draft_new":
        async with get_session_factory()() as session:
            user = await UserService.get_or_create_user(session, query.from_user.id)
            await DraftService.delete_draft(session, user.id)
        context.user_data.clear()
        context.user_data["lang"] = lang
        context.user_data["attachments"] = []
        return await show_complaint_lang_selection(update, context)

    # Complaint language selected
    clang_map = {
        "clang_uz_latin": ComplaintLanguage.UZ_LATIN.value,
        "clang_uz_cyrillic": ComplaintLanguage.UZ_CYRILLIC.value,
        "clang_ru": ComplaintLanguage.RU.value,
    }
    context.user_data["complaint_language"] = clang_map.get(query.data, ComplaintLanguage.UZ_LATIN.value)
    lang = content_locale(context)
    return await show_complaint_type_selection(update, context, lang)


async def show_complaint_type_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> int:
    """Show the complaint type choices for both a new form and a resumed draft."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_ariza", lang), callback_data="ctype_ariza")],
        [InlineKeyboardButton(t("btn_shikoyat", lang), callback_data="ctype_shikoyat")],
        [InlineKeyboardButton(t("btn_taklif", lang), callback_data="ctype_taklif")],
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(
            t("select_complaint_type", lang), reply_markup=keyboard, parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            t("select_complaint_type", lang), reply_markup=keyboard, parse_mode="Markdown"
        )
    return COMPLAINT_TYPE


async def resume_saved_draft(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Resume a draft at its first incomplete step after revalidating live catalogs."""
    data = context.user_data
    lang = content_locale(context)
    if data.get("complaint_language") not in {
        ComplaintLanguage.UZ_LATIN.value,
        ComplaintLanguage.UZ_CYRILLIC.value,
        ComplaintLanguage.RU.value,
    }:
        return await show_complaint_lang_selection(update, context)
    if data.get("complaint_type") not in {
        ComplaintType.ARIZA.value,
        ComplaintType.SHIKOYAT.value,
        ComplaintType.TAKLIF.value,
    }:
        return await show_complaint_type_selection(update, context, lang)

    name = data.get("full_name")
    if not isinstance(name, str) or len(name.strip().split()) < 2 or len(name.strip()) > 255:
        data.pop("full_name", None)
        prompt = field_prompt(t("enter_full_name", lang), "full_name", context)
        if update.callback_query:
            await update.callback_query.edit_message_text(prompt, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(prompt, parse_mode="Markdown")
        return ENTER_NAME

    phone = data.get("phone_number")
    if not isinstance(phone, str) or not PHONE_REGEX.fullmatch(phone):
        data.pop("phone_number", None)
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton(t("btn_share_contact", lang), request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        prompt = field_prompt(t("enter_phone", lang), "phone", context)
        if update.callback_query:
            await update.callback_query.edit_message_text(
                "Telefon raqamingizni ulashish uchun pastdagi tugmani bosing."
                if lang != "ru" else "Нажмите кнопку ниже, чтобы поделиться своим номером."
            )
            await update.callback_query.message.reply_text(
                prompt, reply_markup=keyboard, parse_mode="Markdown"
            )
        else:
            await update.effective_message.reply_text(
                prompt, reply_markup=keyboard, parse_mode="Markdown"
            )
        return ENTER_PHONE

    config = get_runtime_bot_config()
    async with get_session_factory()() as session:
        active_mfy_ids = {
            area.id for area in await CatalogService.get_active_mfy_areas(session)
        }
        active_categories = {
            category.id: category
            for category in await CatalogService.get_active_categories(session)
        }

    if config["form_fields"]["mfy"]["required"] and data.get("mfy_area_id") not in active_mfy_ids:
        data.pop("mfy_area_id", None)
        return await prompt_mfy_selection(update, context, lang)
    address = data.get("address_detail")
    if not isinstance(address, str) or not 8 <= len(address.strip()) <= 500:
        data.pop("address_detail", None)
        return await prompt_address(update, context, lang)
    if data.get("category_id") not in active_categories:
        data.pop("category_id", None)
        return await prompt_category_selection(update, context, lang)
    title = data.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 500:
        data.pop("title", None)
        prompt = field_prompt(t("enter_title", lang), "title", context)
        if update.callback_query:
            await update.callback_query.edit_message_text(prompt, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(prompt, parse_mode="Markdown")
        return ENTER_TITLE
    description = data.get("complaint_text")
    if not isinstance(description, str) or not description.strip() or len(description.strip()) > 4000:
        data.pop("complaint_text", None)
        prompt = field_prompt(t("enter_description", lang), "description", context)
        if update.callback_query:
            await update.callback_query.edit_message_text(prompt, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(prompt, parse_mode="Markdown")
        return ENTER_DESCRIPTION

    attachments_field = config["form_fields"]["attachments"]
    if not attachments_field["enabled"]:
        data.pop("attachments", None)
    elif not data.get("attachments"):
        return await prompt_attachments(update, context, lang)
    return await show_preview(update, context)


async def handle_complaint_type(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle complaint type selection."""
    query = update.callback_query
    await query.answer()
    lang = content_locale(context)

    type_map = {
        "ctype_ariza": ComplaintType.ARIZA.value,
        "ctype_shikoyat": ComplaintType.SHIKOYAT.value,
        "ctype_taklif": ComplaintType.TAKLIF.value,
    }
    context.user_data["complaint_type"] = type_map.get(query.data, ComplaintType.ARIZA.value)

    await query.edit_message_text(field_prompt(t("enter_full_name", lang), "full_name", context), parse_mode="Markdown")
    return ENTER_NAME


async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle full name input."""
    lang = content_locale(context)
    name = (update.message.text or "").strip()

    if len(name.split()) < 2 or len(name) > 255:
        await update.message.reply_text(t("invalid_full_name", lang))
        return ENTER_NAME

    context.user_data["full_name"] = name

    # Save draft
    await save_draft(update.effective_user.id, context.user_data)
    if context.user_data.pop("_editing", False):
        return await show_preview(update, context)

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(t("btn_share_contact", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        field_prompt(t("enter_phone", lang), "phone", context), reply_markup=keyboard, parse_mode="Markdown"
    )
    return ENTER_PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Verify the citizen shared their own contact, then continue without sensitive ID data."""
    lang = content_locale(context)
    if not update.message.contact:
        await update.message.reply_text(t("own_contact_required", lang))
        return ENTER_PHONE
    if update.message.contact.user_id != update.effective_user.id:
        await update.message.reply_text(t("own_contact_required", lang))
        return ENTER_PHONE

    phone = update.message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    if not PHONE_REGEX.match(phone):
        await update.message.reply_text(t("invalid_phone", lang))
        return ENTER_PHONE

    context.user_data["phone_number"] = phone
    context.user_data.pop("passport_data", None)
    context.user_data.pop("birth_date", None)
    await save_draft(update.effective_user.id, context.user_data)
    if context.user_data.pop("_editing", False):
        return await show_preview(update, context)
    return await prompt_mfy_selection(update, context, lang)


async def prompt_mfy_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> int:
    """Show the configured active settlement list when the field is enabled."""
    config = get_runtime_bot_config()
    field = config["form_fields"]["mfy"]
    if not field["enabled"]:
        context.user_data.pop("mfy_area_id", None)
        return await prompt_address(update, context, lang)

    async with get_session_factory()() as session:
        mfy_areas = await CatalogService.get_active_mfy_areas(session)

    buttons = [
        [InlineKeyboardButton(localized_name(area, context), callback_data=f"mfy_{area.id}")]
        for area in mfy_areas
    ]
    message = t("select_mfy", lang)
    if not mfy_areas:
        context.user_data.pop("mfy_area_id", None)
        if field["required"]:
            unavailable = "Hududlar ro‘yxati tasdiqlanib kiritilmagan. Iltimos, keyinroq qayta urinib ko‘ring." if lang != "ru" else "Список районов ещё не подтверждён. Пожалуйста, попробуйте позже."
            if update.callback_query:
                await update.callback_query.edit_message_text(unavailable)
            else:
                await update.effective_message.reply_text(unavailable)
            return ConversationHandler.END
        message += "\n\nHududlar ro‘yxati hozircha kiritilmagan. MFY nomini manzil izohiga yozishingiz mumkin."
        return await prompt_address(update, context, lang, prefix_message=message)
    keyboard = InlineKeyboardMarkup(buttons)
    message = field_prompt(message, "mfy", context)
    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")
    return SELECT_MFY


async def handle_passport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ignore legacy callbacks; passport collection stays disabled pending legal approval."""
    context.user_data.pop("passport_data", None)
    if update.callback_query:
        await update.callback_query.answer()
    return await prompt_mfy_selection(update, context, context.user_data.get("lang", "uz"))


async def handle_birth_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ignore legacy callbacks; birth date collection stays disabled pending legal approval."""
    context.user_data.pop("birth_date", None)
    if update.callback_query:
        await update.callback_query.answer()
    return await prompt_mfy_selection(update, context, context.user_data.get("lang", "uz"))


async def handle_mfy_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Store a required selection, rejecting buttons from an outdated catalog."""
    query = update.callback_query
    if query.data == "mfy_skip":
        lang = context.user_data.get("lang", "uz")
        await query.answer(
            "Mahallani tanlash shart." if lang != "ru" else "Выберите махаллю.",
            show_alert=True,
        )
        return SELECT_MFY
    try:
        area_id = int(query.data.removeprefix("mfy_"))
    except ValueError:
        lang = content_locale(context)
        await query.answer(
            "Mahallani ro‘yxatdan tanlang."
            if lang != "ru" else "Выберите махаллю из списка.",
            show_alert=True,
        )
        return SELECT_MFY
    async with get_session_factory()() as session:
        active_ids = {
            area.id for area in await CatalogService.get_active_mfy_areas(session)
    }
    if area_id not in active_ids:
        lang = content_locale(context)
        await query.answer(
            "Mahalla ro‘yxati yangilandi. Iltimos, faol ro‘yxatdan tanlang."
            if lang != "ru" else "Список махаллей обновлён. Выберите из актуального списка.",
            show_alert=True,
        )
        return await prompt_mfy_selection(update, context, lang)
    await query.answer()
    context.user_data["mfy_area_id"] = area_id
    await save_draft(query.from_user.id, context.user_data)
    if context.user_data.pop("_editing", False):
        return await show_preview(update, context)
    return await prompt_address(update, context, context.user_data.get("lang", "uz"))


async def prompt_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lang: str,
    prefix_message: str | None = None,
) -> int:
    config = get_runtime_bot_config()
    field = config["form_fields"]["address"]
    if not field["enabled"]:
        context.user_data.pop("address_detail", None)
        return await prompt_category_selection(update, context, lang)

    message = prefix_message or t("enter_address_detail", lang)
    message = field_prompt(message, "address", context)
    keyboard = None
    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")
    return ENTER_ADDRESS


async def prompt_category_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> int:
    async with get_session_factory()() as session:
        categories = await CatalogService.get_active_categories(session)
    if not categories:
        await update.effective_message.reply_text(
            "Murojaat yo‘nalishlari kiritilmagan. Iltimos, keyinroq urinib ko‘ring."
            if lang != "ru" else "Категории пока не настроены. Попробуйте позже."
        )
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(localized_name(category, context), callback_data=f"cat_{category.id}")]
        for category in categories
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    message = field_prompt(t("select_category", lang), "description", context)
    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")
    return SELECT_CATEGORY


async def handle_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Require a non-empty address detail before continuing."""
    if update.callback_query:
        query = update.callback_query
        if query.data == "address_skip":
            lang = content_locale(context)
            await query.answer(t("invalid_address_detail", lang), show_alert=True)
            return ENTER_ADDRESS
        await query.answer()
        return ENTER_ADDRESS
    else:
        detail = (update.message.text or "").strip()
        lang = content_locale(context)
        if len(detail) < 8 or len(detail) > 500:
            await update.message.reply_text(t("invalid_address_detail", lang))
            return ENTER_ADDRESS
        context.user_data["address_detail"] = detail
        await save_draft(update.effective_user.id, context.user_data)
    if context.user_data.pop("_editing", False):
        return await show_preview(update, context)
    return await prompt_category_selection(update, context, context.user_data.get("lang", "uz"))



async def handle_category(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle category selection and check for duplicates."""
    query = update.callback_query
    lang = content_locale(context)

    try:
        category_id = int(query.data.removeprefix("cat_"))
    except ValueError:
        await query.answer("Yo‘nalishni ro‘yxatdan tanlang." if lang != "ru" else "Выберите направление из списка.", show_alert=True)
        return await prompt_category_selection(update, context, lang)

    async with get_session_factory()() as session:
        active_ids = {
            category.id for category in await CatalogService.get_active_categories(session)
        }
    if category_id not in active_ids:
        await query.answer(
            "Yo‘nalishlar ro‘yxati yangilandi. Faol yo‘nalishni tanlang."
            if lang != "ru" else "Список направлений обновлён. Выберите актуальное направление.",
            show_alert=True,
        )
        return await prompt_category_selection(update, context, lang)

    await query.answer()
    context.user_data["category_id"] = category_id
    await save_draft(query.from_user.id, context.user_data)
    if context.user_data.pop("_editing", False):
        return await show_preview(update, context)

    # Check duplicate hint (never auto-reject)
    async with get_session_factory()() as session:
        user = await UserService.get_or_create_user(session, query.from_user.id)
        duplicate = await ComplaintService.check_duplicate_hint(
            session, user.id, category_id
        )

        # Check for emergency category
        category = await CatalogService.get_category_by_id(session, category_id)
        if category and category.is_emergency:
            guidance = category.emergency_guidance_uz if lang != "ru" else category.emergency_guidance_ru
            if guidance:
                await query.message.reply_text(guidance)

    if duplicate:
        dup_status = get_status_label(duplicate.status, lang)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("btn_new_anyway", lang), callback_data="dup_new")],
            [InlineKeyboardButton(t("btn_view_existing", lang), callback_data=f"dup_view_{duplicate.tracking_id}")],
        ])
        await query.edit_message_text(
            t("duplicate_warning", lang,
              tracking_id=duplicate.tracking_id,
              title=escape_markdown(duplicate.title, version=1),
              status=dup_status),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return ENTER_TITLE  # Handle dup choice in title state

    await query.edit_message_text(field_prompt(t("enter_title", lang), "title", context), parse_mode="Markdown")
    return ENTER_TITLE


async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle title input or duplicate choice."""
    lang = content_locale(context)

    # Handle callback from duplicate warning
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "dup_new":
            await query.edit_message_text(field_prompt(t("enter_title", lang), "title", context), parse_mode="Markdown")
            return ENTER_TITLE
        elif query.data.startswith("dup_view_"):
            await query.edit_message_text(t("main_menu", lang), parse_mode="Markdown")
            await my_complaints(update, context)
            await query.message.reply_text(
                t("main_menu", lang), reply_markup=main_menu_keyboard(lang), parse_mode="Markdown"
            )
            return ConversationHandler.END

    title = update.message.text.strip()
    if not title or len(title) > 500:
        await update.message.reply_text(
            "Sarlavhani kiriting (1–500 belgi)." if lang != "ru"
            else "Введите заголовок (от 1 до 500 символов)."
        )
        return ENTER_TITLE

    context.user_data["title"] = title
    await save_draft(update.effective_user.id, context.user_data)
    if context.user_data.pop("_editing", False):
        return await show_preview(update, context)

    await update.message.reply_text(field_prompt(t("enter_description", lang), "description", context), parse_mode="Markdown")
    return ENTER_DESCRIPTION



async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = content_locale(context)
    context.user_data["complaint_text"] = (update.message.text or "").strip()
    if not context.user_data["complaint_text"] or len(context.user_data["complaint_text"]) > 4000:
        context.user_data.pop("complaint_text", None)
        await update.message.reply_text(
            "Murojaat matnini kiriting (1–4000 belgi)." if lang != "ru"
            else "Введите текст обращения (от 1 до 4000 символов)."
        )
        return ENTER_DESCRIPTION
    await save_draft(update.effective_user.id, context.user_data)
    if context.user_data.pop("_editing", False):
        return await show_preview(update, context)
    field = get_runtime_bot_config()["form_fields"]["attachments"]
    if not field["enabled"]:
        context.user_data.pop("attachments", None)
        return await show_preview(update, context)
    return await prompt_attachments(update, context, lang)


async def prompt_attachments(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str
) -> int:
    """Offer a clear optional/required file step for a new or resumed draft."""
    field = get_runtime_bot_config()["form_fields"]["attachments"]
    prompt = field_prompt(t("ask_attachments", lang), "attachments", context)
    label = ("Ilovalarni yakunlash" if field["required"] else "⏭ Ilovasiz davom etish") if lang != "ru" else ("Завершить вложения" if field["required"] else "⏭ Продолжить без вложений")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="finish_attach")]])
    if update.callback_query:
        await update.callback_query.edit_message_text(
            prompt, reply_markup=keyboard, parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            prompt, reply_markup=keyboard, parse_mode="Markdown"
        )
    return ATTACHMENTS


async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = content_locale(context)
    field = get_runtime_bot_config()["form_fields"]["attachments"]
    if not field["enabled"]:
        context.user_data.pop("attachments", None)
        return await show_preview(update, context)
    if update.callback_query and update.callback_query.data in {"skip_attach", "finish_attach"}:
        query = update.callback_query
        if field["required"] and not context.user_data.get("attachments"):
            await query.answer("Kamida bitta fayl ilova qiling." if lang != "ru" else "Прикрепите хотя бы один файл.", show_alert=True)
            return ATTACHMENTS
        await query.answer()
        await save_draft(query.from_user.id, context.user_data)
        return await show_preview(update, context)
    if update.message:
        message=update.message; file_info=None; file_type=None
        if message.photo: file_info=message.photo[-1]; file_type="photo"
        elif message.document: file_info=message.document; file_type="document"
        elif message.video: file_info=message.video; file_type="video"
        if file_info:
            settings=get_settings(); size=getattr(file_info,"file_size",0) or 0
            if size>settings.max_file_size:
                await message.reply_text(t("attachment_invalid",lang)); return ATTACHMENTS
            if file_type=="document" and hasattr(file_info,"file_name"):
                ext=(file_info.file_name or "").rsplit(".",1)[-1].lower()
                if ext not in settings.allowed_extensions_list:
                    await message.reply_text(t("attachment_invalid",lang)); return ATTACHMENTS
            attachments=context.user_data.get("attachments",[])
            if len(attachments)>=10:
                await message.reply_text("Ko‘pi bilan 10 ta fayl ilova qiling." if lang=="uz" else "Можно прикрепить не более 10 файлов.")
                return ATTACHMENTS
            attachments.append({"file_id":file_info.file_id,"file_type":file_type,"file_name":getattr(file_info,"file_name",None),"file_size":size,"mime_type":getattr(file_info,"mime_type",None)})
            context.user_data["attachments"]=attachments
            await save_draft(update.effective_user.id, context.user_data)
            label="Ilovalarni yakunlash" if lang=="uz" else "Завершить вложения"
            await message.reply_text(t("attachment_saved",lang),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label,callback_data="finish_attach")]]))
        else:
            await message.reply_text(
                "Rasm, hujjat yoki video yuboring yoxud davom etish tugmasini bosing."
                if lang != "ru" else "Отправьте фото, документ или видео либо нажмите кнопку продолжения."
            )
    return ATTACHMENTS


async def show_preview(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Show complaint preview for user confirmation."""
    lang = content_locale(context)
    data = context.user_data
    fields = get_runtime_bot_config()["form_fields"]
    if not fields["mfy"]["enabled"]: data.pop("mfy_area_id", None)
    if not fields["address"]["enabled"]: data.pop("address_detail", None)
    if not fields["attachments"]["enabled"]: data.pop("attachments", None)

    # Get category and MFY names
    cat_name = ""
    mfy_name = ""
    async with get_session_factory()() as session:
        if data.get("category_id"):
            cat = await CatalogService.get_category_by_id(session, data["category_id"])
            if cat:
                cat_name = cat.name_ru if lang == "ru" else cat.name_uz
        if data.get("mfy_area_id"):
            mfy_areas = await CatalogService.get_active_mfy_areas(session)
            for mfy in mfy_areas:
                if mfy.id == data.get("mfy_area_id"):
                    mfy_name = mfy.name_ru if lang == "ru" else mfy.name_uz
                    break

    type_label = get_type_label(data.get("complaint_type", "ariza"), lang)
    attachments_count = len(data.get("attachments", []))

    preview = t("preview_header", lang)
    preview += t("preview_type", lang, type=escape_markdown(type_label, version=1))
    preview += t("preview_name", lang, name=escape_markdown(data.get("full_name", "—"), version=1))
    preview += t("preview_phone", lang, phone=escape_markdown(data.get("phone_number", "—"), version=1))
    preview += t(
        "preview_address", lang,
        mfy=escape_markdown(mfy_name or "—", version=1),
        address=escape_markdown(data.get("address_detail", "—"), version=1),
    )
    preview += t("preview_category", lang, category=escape_markdown(cat_name or "—", version=1))
    preview += t("preview_title", lang, title=escape_markdown(data.get("title", "—"), version=1))
    preview += t(
        "preview_text", lang,
        text=escape_markdown(data.get("complaint_text", "—")[:200], version=1),
    )
    if attachments_count > 0:
        preview += t("preview_attachments", lang, count=attachments_count)
    preview += t("preview_footer", lang)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_confirm_send", lang), callback_data="confirm_send")],
        [InlineKeyboardButton(t("btn_edit", lang), callback_data="edit_complaint")],
        [InlineKeyboardButton(t("btn_cancel", lang), callback_data="cancel_complaint")],
    ])

    if update.callback_query:
        await update.callback_query.edit_message_text(
            preview, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            preview, reply_markup=keyboard, parse_mode="Markdown"
        )

    return PREVIEW


async def handle_preview_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle preview confirmation, edit, or cancel."""
    query = update.callback_query
    await query.answer()
    lang = content_locale(context)

    if query.data == "cancel_complaint":
        async with get_session_factory()() as session:
            user = await UserService.get_or_create_user(session, query.from_user.id)
            await DraftService.delete_draft(session, user.id)
        context.user_data.clear()
        await query.edit_message_text(t("cancelled", lang))
        await query.message.reply_text(
            t("main_menu", lang),
            reply_markup=main_menu_keyboard(lang),
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if query.data == "edit_complaint":
        edit_buttons = [
            [InlineKeyboardButton(t("btn_edit_name", lang), callback_data="edit_name")],
            [InlineKeyboardButton(t("btn_edit_phone", lang), callback_data="edit_phone")],
            [InlineKeyboardButton(t("btn_edit_address", lang), callback_data="edit_address")],
            [InlineKeyboardButton(t("btn_edit_category", lang), callback_data="edit_category")],
            [InlineKeyboardButton(t("btn_edit_title", lang), callback_data="edit_title")],
            [InlineKeyboardButton(t("btn_edit_text", lang), callback_data="edit_text")],
            [InlineKeyboardButton(t("btn_done_editing", lang), callback_data="edit_done")],
        ]
        keyboard = InlineKeyboardMarkup(edit_buttons)
        await query.edit_message_text(t("edit_what", lang), reply_markup=keyboard, parse_mode="Markdown")
        return EDITING

    if query.data == "confirm_send":
        return await submit_complaint(update, context)

    return PREVIEW


async def handle_edit_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle edit field selection."""
    query = update.callback_query
    await query.answer()
    lang = content_locale(context)

    if query.data == "edit_done":
        return await show_preview(update, context)

    edit_map = {
        "edit_name": ("enter_full_name", ENTER_NAME),
        "edit_phone": ("enter_phone", ENTER_PHONE),
        "edit_title": ("enter_title", ENTER_TITLE),
        "edit_text": ("enter_description", ENTER_DESCRIPTION),
    }

    if query.data in edit_map:
        text_key, state = edit_map[query.data]
        context.user_data["_editing"] = True
        await query.edit_message_text(t(text_key, lang), parse_mode="Markdown")
        if state == ENTER_PHONE:
            await query.message.reply_text(
                t("own_contact_required", lang),
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton(t("btn_share_contact", lang), request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True,
                ),
            )
        return state

    if query.data == "edit_address":
        context.user_data["_editing"] = True
        await query.edit_message_text(field_prompt(t("enter_address_detail", lang), "address", context), parse_mode="Markdown")
        return ENTER_ADDRESS

    if query.data == "edit_category":
        context.user_data["_editing"] = True
        async with get_session_factory()() as session:
            categories = await CatalogService.get_active_categories(session)
        buttons = []
        for cat in categories:
            name = cat.name_ru if lang == "ru" else cat.name_uz
            buttons.append([InlineKeyboardButton(name, callback_data=f"cat_{cat.id}")])
        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            t("select_category", lang), reply_markup=keyboard, parse_mode="Markdown"
        )
        return SELECT_CATEGORY

    return EDITING


async def submit_complaint(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Revalidate eligibility, reserve a rate-limit slot, and submit atomically."""
    query = update.callback_query
    lang = content_locale(context)
    data = context.user_data
    bot_config = get_runtime_bot_config()
    fields = bot_config["form_fields"]
    if any(
        not isinstance(data.get(key), str) or not data[key].strip()
        for key in ("full_name", "phone_number", "title", "complaint_text")
    ) or not PHONE_REGEX.fullmatch(str(data.get("phone_number", ""))) or len(
        str(data.get("complaint_text", ""))
    ) > 4000:
        return await resume_saved_draft(update, context)
    if fields["mfy"]["required"] and not data.get("mfy_area_id"):
        return await prompt_mfy_selection(update, context, lang)
    address = data.get("address_detail")
    if fields["address"]["required"] and (
        not isinstance(address, str) or not 8 <= len(address.strip()) <= 500
    ):
        data.pop("address_detail", None)
        return await prompt_address(update, context, lang)
    if fields["attachments"]["required"] and not data.get("attachments"):
        return await prompt_attachments(update, context, lang)
    if not isinstance(data.get("category_id"), int) or data["category_id"] <= 0:
        return await prompt_category_selection(update, context, lang)
    if fields["mfy"]["required"]:
        async with get_session_factory()() as session:
            active_mfy_ids = {
                area.id for area in await CatalogService.get_active_mfy_areas(session)
            }
        if data.get("mfy_area_id") not in active_mfy_ids:
            data.pop("mfy_area_id", None)
            return await prompt_mfy_selection(update, context, lang)
    if bot_config["maintenance_enabled"] or not bot_config["features"]["submissions"]:
        message = (
            bot_config["maintenance_message_uz"] if lang != "ru"
            else bot_config["maintenance_message_ru"]
        ) if bot_config["maintenance_enabled"] else (
            "Yangi murojaat yuborish vaqtincha mavjud emas."
            if lang != "ru" else "Подача новых обращений временно недоступна."
        )
        await query.edit_message_text(message, reply_markup=None)
        ui_lang = context.user_data.get("lang", "uz")
        await query.message.reply_text(
            t("main_menu", ui_lang), reply_markup=main_menu_keyboard(ui_lang)
        )
        return ConversationHandler.END
    committed = False
    complaint = None
    cat_name = "—"

    try:
        async with get_session_factory()() as session:
            user = await UserService.get_or_create_user(session, query.from_user.id)
            if not await UserService.has_valid_consent(session, user.id, CONSENT_VERSION):
                await query.edit_message_text(
                    "Ariza yuborish uchun /start orqali rozilik bering."
                    if lang != "ru"
                    else "Чтобы отправить обращение, подтвердите согласие через /start."
                )
                return ConversationHandler.END

            complaint_type = data.get("complaint_type", ComplaintType.ARIZA.value)
            selected_category = await CatalogService.get_category_by_id(
                session, data["category_id"]
            )
            if selected_category is None or not selected_category.is_active:
                await query.edit_message_text(
                    "Tanlangan yo‘nalish hozir faol emas. /start orqali qayta urinib ko‘ring."
                    if lang != "ru"
                    else "Выбранное направление сейчас неактивно. Начните заново через /start."
                )
                return ConversationHandler.END
            deadline_days = deadline_days_for(
                bot_config, complaint_type, selected_category.sla_days
            )

            async with RateLimitService.reserve_complaint_slot(
                session, query.from_user.id, max_per_day=bot_config["daily_limit"]
            ) as (allowed, _count):
                if not allowed:
                    await query.edit_message_text(
                        t("rate_limit_exceeded", lang, max=bot_config["daily_limit"])
                    )
                    return ConversationHandler.END

                complaint = await ComplaintService.create_complaint(
                    session=session,
                    user_id=user.id,
                    complaint_type=complaint_type,
                    complaint_language=data.get("complaint_language", ComplaintLanguage.UZ_LATIN.value),
                    category_id=data["category_id"],
                    full_name=data["full_name"],
                    phone_number=data["phone_number"],
                    title=data["title"],
                    complaint_text=data["complaint_text"],
                    mfy_area_id=data.get("mfy_area_id") if fields["mfy"]["enabled"] else None,
                    address_detail=data.get("address_detail") if fields["address"]["enabled"] else None,
                    birth_date=None,
                    passport_data=None,
                    deadline_days=deadline_days,
                    commit=False,
                )

                from app.models import Attachment
                for att in data.get("attachments", []):
                    session.add(Attachment(
                        complaint_id=complaint.id,
                        file_type=att["file_type"],
                        file_name=att.get("file_name"),
                        file_size=att.get("file_size"),
                        telegram_file_id=att["file_id"],
                        mime_type=att.get("mime_type"),
                        uploaded_by="citizen",
                    ))

                await DraftService.delete_draft(session, user.id, commit=False)
                await session.commit()
                committed = True
                context.user_data.clear()

                cat_name = selected_category.name_ru if lang == "ru" else selected_category.name_uz

        type_label = get_type_label(complaint.complaint_type, lang)
        date_str = format_tashkent_time(complaint.submitted_at)

        receipt = t(
            "complaint_submitted", lang,
            tracking_id=complaint.tracking_id,
            date=date_str,
            type=escape_markdown(type_label, version=1),
            category=escape_markdown(cat_name, version=1),
        )

        await query.edit_message_text(receipt, parse_mode="Markdown")
        await query.message.reply_text(
            t("main_menu", lang),
            reply_markup=main_menu_keyboard(lang),
            parse_mode="Markdown",
        )

        logger.info(
            "Complaint submitted",
            complaint_id=complaint.id,
            tracking_id=complaint.tracking_id,
        )

        context.user_data.clear()
        return ConversationHandler.END

    except Exception:
        logger.exception("Error submitting complaint")
        if committed and complaint is not None:
            context.user_data.clear()
            try:
                await query.message.reply_text(
                    f"Murojaat qabul qilindi. Tracking ID: {complaint.tracking_id}"
                    if lang != "ru"
                    else f"Обращение принято. Tracking ID: {complaint.tracking_id}"
                )
            except Exception:
                logger.warning("Receipt delivery failed after committed submission")
            return ConversationHandler.END
        await query.edit_message_text(t("error_generic", lang))
        return ConversationHandler.END


async def handle_citizen_resolution(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Accept a resolution decision only from the complaint owner."""
    from sqlalchemy import select

    query = update.callback_query
    action, raw_id = query.data.split("_", 2)[1:]
    try:
        complaint_id = int(raw_id)
    except ValueError:
        await query.answer("Invalid complaint", show_alert=True)
        return

    async with get_session_factory()() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == query.from_user.id)
        )).scalar_one_or_none()
        complaint = None
        if user:
            complaint = (await session.execute(
                select(Complaint).where(
                    Complaint.id == complaint_id,
                    Complaint.user_id == user.id,
                    Complaint.deleted_at.is_(None),
                )
            )).scalar_one_or_none()
        if not complaint:
            await query.answer("Bu murojaat sizga tegishli emas.", show_alert=True)
            return
        if complaint.archived_at is not None:
            language = user.interface_language or "uz"
            message = "Murojaat arxivlangan." if language != "ru" else "Обращение архивировано."
            await query.answer(message, show_alert=True)
            return
        if complaint.status != ComplaintStatus.CITIZEN_CONFIRMATION_PENDING.value:
            await query.answer("Bu murojaat bo‘yicha javob allaqachon qayd etilgan.", show_alert=True)
            return

        language = user.interface_language or "uz"
        if action == "confirm":
            await ComplaintService.confirm_resolved(session, complaint.id, user.id)
            result_text = "✅ Murojaat hal qilingan deb tasdiqlandi."
            if language == "ru":
                result_text = "✅ Вы подтвердили, что вопрос решён."
        elif action == "reopen":
            reason = (
                "Fuqaro masala hal bo‘lmaganini bildirdi"
                if language != "ru"
                else "Гражданин сообщил, что вопрос не решён"
            )
            await ComplaintService.reopen_complaint(session, complaint.id, user.id, reason)
            result_text = "🔄 Murojaat qayta ko‘rib chiqish uchun yuborildi."
            if language == "ru":
                result_text = "🔄 Обращение направлено на повторное рассмотрение."
        else:
            await query.answer("Invalid action", show_alert=True)
            return

    await query.answer()
    await query.edit_message_text(result_text)


async def start_additional_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Open an active request only for the citizen who owns the complaint."""
    from sqlalchemy import select
    from app.change_models import ComplaintCitizenMessage

    query = update.callback_query
    try:
        complaint_id = int(query.data.removeprefix("additional_info_"))
    except ValueError:
        await query.answer("Invalid request", show_alert=True)
        return ConversationHandler.END

    async with get_session_factory()() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == query.from_user.id)
        )).scalar_one_or_none()
        complaint = None
        request = None
        if user:
            complaint = (await session.execute(
                select(Complaint).where(
                    Complaint.id == complaint_id,
                    Complaint.user_id == user.id,
                    Complaint.status == ComplaintStatus.WAITING_FOR_CITIZEN.value,
                    Complaint.archived_at.is_(None),
                    Complaint.deleted_at.is_(None),
                )
            )).scalar_one_or_none()
        if complaint:
            answered = select(ComplaintCitizenMessage.reply_to_id).where(
                ComplaintCitizenMessage.complaint_id == complaint_id,
                ComplaintCitizenMessage.reply_to_id.is_not(None),
            )
            request = (await session.execute(
                select(ComplaintCitizenMessage).where(
                    ComplaintCitizenMessage.complaint_id == complaint_id,
                    ComplaintCitizenMessage.message_type == "request",
                    ComplaintCitizenMessage.id.not_in(answered),
                ).order_by(ComplaintCitizenMessage.created_at.desc()).limit(1)
            )).scalar_one_or_none()

    if not user or not complaint or not request:
        await query.answer(t("additional_info_expired", "uz"), show_alert=True)
        return ConversationHandler.END

    language = user.interface_language or "uz"
    context.user_data["additional_info_complaint_id"] = complaint_id
    context.user_data["additional_info_request_id"] = request.id
    context.user_data["additional_info_language"] = language
    await query.answer()
    await query.message.reply_text(t("additional_info_prompt", language))
    return ADDITIONAL_INFO


async def receive_additional_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Accept one text or one Telegram attachment for the pending request."""
    from sqlalchemy import select

    message = update.effective_message
    language = context.user_data.get("additional_info_language", "uz")
    complaint_id = context.user_data.get("additional_info_complaint_id")
    request_id = context.user_data.get("additional_info_request_id")
    if not message or not complaint_id or not request_id:
        return ConversationHandler.END

    file_info = None
    file_type = None
    if message.photo:
        file_info, file_type = message.photo[-1], "photo"
    elif message.document:
        file_info, file_type = message.document, "document"
    elif message.video:
        file_info, file_type = message.video, "video"

    body = (message.text or message.caption or "").strip()
    attachment = None
    if file_info:
        settings = get_settings()
        size = getattr(file_info, "file_size", 0) or 0
        filename = getattr(file_info, "file_name", None)
        if size > settings.max_file_size:
            await message.reply_text(t("additional_info_invalid", language))
            return ADDITIONAL_INFO
        if file_type == "document" and filename:
            extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if extension not in settings.allowed_extensions_list:
                await message.reply_text(t("additional_info_invalid", language))
                return ADDITIONAL_INFO
        attachment = {
            "file_id": file_info.file_id,
            "file_type": file_type,
            "file_name": filename,
            "file_size": size,
            "mime_type": getattr(file_info, "mime_type", None),
        }
    if len(body) > 5000 or (not body and not attachment):
        await message.reply_text(t("additional_info_invalid", language))
        return ADDITIONAL_INFO

    from app.citizen_info import CitizenInfoService

    try:
        async with get_session_factory()() as session:
            user = (await session.execute(
                select(User).where(User.telegram_id == update.effective_user.id)
            )).scalar_one_or_none()
            if not user:
                raise ValueError("Citizen account not found")
            await CitizenInfoService.submit_citizen_reply(
                session,
                complaint_id,
                request_id,
                user.id,
                body,
                attachment,
            )
    except ValueError:
        context.user_data.pop("additional_info_complaint_id", None)
        context.user_data.pop("additional_info_request_id", None)
        context.user_data.pop("additional_info_language", None)
        await message.reply_text(t("additional_info_expired", language))
        return ConversationHandler.END

    context.user_data.pop("additional_info_complaint_id", None)
    context.user_data.pop("additional_info_request_id", None)
    context.user_data.pop("additional_info_language", None)
    await message.reply_text(t("additional_info_received", language))
    return ConversationHandler.END


async def cancel_additional_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    language = context.user_data.pop("additional_info_language", "uz")
    context.user_data.pop("additional_info_complaint_id", None)
    context.user_data.pop("additional_info_request_id", None)
    await update.effective_message.reply_text(t("cancelled", language))
    return ConversationHandler.END


async def restart_from_additional_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.user_data.pop("additional_info_complaint_id", None)
    context.user_data.pop("additional_info_request_id", None)
    context.user_data.pop("additional_info_language", None)
    return await start_command(update, context)


async def route_menu_during_additional_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Keep persistent menu buttons from being stored as a citizen's reply."""
    action = configured_menu_action(update.effective_message.text)
    if action == "btn_change_language":
        return await restart_from_additional_info(update, context)
    if action == "btn_new_complaint":
        language = context.user_data.get("additional_info_language", "uz")
        await update.effective_message.reply_text(
            "Avval qo‘shimcha ma’lumotni yuboring yoki /cancel bosing."
            if language != "ru"
            else "Сначала отправьте дополнительную информацию или нажмите /cancel."
        )
        return ADDITIONAL_INFO

    context.user_data.pop("additional_info_complaint_id", None)
    context.user_data.pop("additional_info_request_id", None)
    context.user_data.pop("additional_info_language", None)
    if action == "btn_my_complaints":
        await my_complaints(update, context)
    elif action == "btn_help":
        await help_command(update, context)
    return ConversationHandler.END


async def route_menu_during_complaint(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle a reply-keyboard tap safely while a complaint draft is open."""
    action = configured_menu_action(update.effective_message.text)
    if action == "btn_change_language":
        return await restart_complaint_with_start(update, context)

    if update.effective_user and any(
        context.user_data.get(key)
        for key in (
            "complaint_language", "complaint_type", "full_name", "phone_number",
            "mfy_area_id", "address_detail", "category_id", "title",
            "complaint_text", "attachments",
        )
    ):
        await save_draft(update.effective_user.id, context.user_data)

    if action == "btn_my_complaints":
        await my_complaints(update, context)
    elif action == "btn_help":
        await help_command(update, context)
    elif action is None:
        await quick_answer_handler(update, context)
    else:
        lang = context.user_data.get("lang", "uz")
        await update.effective_message.reply_text(
            "Qoralamangiz saqlandi. Uni davom ettirish yoki yangidan boshlash uchun «Murojaat yuborish» tugmasini bosing."
            if lang != "ru"
            else "Черновик сохранён. Нажмите «Подать обращение», чтобы продолжить или начать заново."
        )
    return ConversationHandler.END


async def deliver_pending_notifications(bot, now: datetime | None = None) -> None:
    """Send queued Telegram notifications and persist retry state."""
    from sqlalchemy import or_, select

    now = now or datetime.now(timezone.utc)

    async with get_session_factory()() as session:
        result = await session.execute(
            select(Notification)
            .where(
                Notification.sent.is_(False),
                Notification.retry_count < Notification.max_retries,
                or_(
                    Notification.next_retry_at.is_(None),
                    Notification.next_retry_at <= now,
                ),
            )
            .order_by(Notification.id)
            .limit(25)
        )
        for notification in result.scalars().all():
            markup = None
            if notification.notification_type in {
                "citizen_confirmation", "citizen_confirmation_reminder"
            } and notification.complaint_id:
                if notification.language == "ru":
                    yes, no = "✅ Вопрос решён", "🔄 Не решён"
                else:
                    yes, no = "✅ Hal bo‘ldi", "🔄 Hal bo‘lmadi"
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(yes, callback_data=f"citizen_confirm_{notification.complaint_id}"),
                    InlineKeyboardButton(no, callback_data=f"citizen_reopen_{notification.complaint_id}"),
                ]])
            elif notification.notification_type == "additional_info_request" and notification.complaint_id:
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        t("additional_info_button", notification.language),
                        callback_data=f"additional_info_{notification.complaint_id}",
                    )
                ]])
            try:
                sent = await bot.send_message(
                    chat_id=notification.recipient_telegram_id,
                    text=notification.message_text[:4000],
                    reply_markup=markup,
                )
                notification.sent = True
                notification.sent_at = now
                notification.telegram_message_id = sent.message_id
                notification.next_retry_at = None
                notification.error_message = None
                if notification.deadline_extension_id:
                    extension = await session.get(
                        DeadlineExtension, notification.deadline_extension_id
                    )
                    if extension:
                        extension.citizen_notified = True
                        extension.citizen_notified_at = now
            except Exception as exc:
                notification.retry_count += 1
                notification.error_message = type(exc).__name__[:200]
                if notification.retry_count < notification.max_retries:
                    delay_seconds = min(
                        30 * (2 ** (notification.retry_count - 1)), 21_600
                    )
                    notification.next_retry_at = now + timedelta(seconds=delay_seconds)
                else:
                    notification.next_retry_at = None
                logger.warning(
                    "Telegram notification delivery failed",
                    notification_id=notification.id,
                    attempt=notification.retry_count,
                )
        await session.commit()


async def notification_worker(bot) -> None:
    """Refresh published settings, queue due reminders, and deliver the outbox."""
    from app.deadline_notifications import (
        queue_confirmation_reminders,
        queue_deadline_notices,
    )

    while True:
        try:
            await refresh_runtime_bot_config()
            config = get_runtime_bot_config()
            async with get_session_factory()() as session:
                await queue_deadline_notices(session, config)
                await queue_confirmation_reminders(session, config)
                await session.commit()
            await deliver_pending_notifications(bot)
        except Exception:
            logger.exception("Notification outbox processing failed")
        await asyncio.sleep(5)



# ============================================================================
# My Complaints
# ============================================================================

async def my_complaints(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show user's complaints list."""
    lang = await get_user_lang(update.effective_user.id)
    bot_config = get_runtime_bot_config()
    if not bot_config["features"]["my_complaints"]:
        await update.effective_message.reply_text(
            "Bu bo‘lim hozircha mavjud emas." if lang != "ru" else "Раздел временно недоступен.",
            reply_markup=main_menu_keyboard(lang),
        )
        return
    confirmation_buttons = []

    async with get_session_factory()() as session:
        user = await UserService.get_or_create_user(session, update.effective_user.id)
        complaints = await ComplaintService.get_user_complaints(session, user.id)

    if not complaints:
        await update.effective_message.reply_text(t("no_complaints", lang))
        return

    text = t("complaints_list_header", lang)
    for c in complaints[:10]:  # Show last 10
        status_label = get_status_label(c.status, lang)
        date_str = format_tashkent_time(c.submitted_at)
        text += t(
            "complaint_list_item", lang,
            tracking_id=c.tracking_id,
            title=escape_markdown(c.title[:50], version=1),
            status=status_label,
            date=date_str,
        )

        # Keep confirmation actions available if an earlier Telegram send failed.
        if c.status == ComplaintStatus.CITIZEN_CONFIRMATION_PENDING.value:
            yes = "✅ Hal bo‘ldi" if lang != "ru" else "✅ Вопрос решён"
            no = "🔄 Hal bo‘lmadi" if lang != "ru" else "🔄 Не решён"
            confirmation_buttons.append([
                InlineKeyboardButton(yes, callback_data=f"citizen_confirm_{c.id}"),
                InlineKeyboardButton(no, callback_data=f"citizen_reopen_{c.id}"),
            ])

    chunks = split_telegram_text(text)
    for index, chunk in enumerate(chunks):
        await update.effective_message.reply_text(
            chunk,
            parse_mode="Markdown" if len(chunks) == 1 else None,
            reply_markup=(InlineKeyboardMarkup(confirmation_buttons)
                          if confirmation_buttons and index == len(chunks) - 1 else None),
        )


# ============================================================================
# Help & Language Change
# ============================================================================

async def quick_answer_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Reply to an enabled, published database-configured menu shortcut."""
    if not update.message or not update.effective_user:
        return
    lang = await get_user_lang(update.effective_user.id)
    locale = "ru" if lang == "ru" else "uz"
    label = (update.message.text or "").strip()
    config = get_runtime_bot_config()
    answer = next((
        item for item in config["quick_answers"]
        if item["enabled"] and label == item[f"label_{locale}"]
    ), None)
    if answer:
        await send_menu_text(update.message, answer[f"response_{locale}"], lang)
    else:
        await update.message.reply_text(
            "Menyu yangilandi. Quyidagi tugmalardan tanlang."
            if locale == "uz" else "Меню обновлено. Выберите кнопку ниже.",
            reply_markup=main_menu_keyboard(lang),
        )


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the published help text plus approved public hours and contacts."""
    lang = await get_user_lang(update.effective_user.id)
    config = get_runtime_bot_config()
    if not config["features"]["help"]:
        await update.message.reply_text(
            "Yordam bo‘limi vaqtincha mavjud emas."
            if lang != "ru" else "Раздел помощи временно недоступен.",
            reply_markup=main_menu_keyboard(lang),
        )
        return
    custom = localized_config(config, "help_message", lang)
    sections = [custom or t("help_text", lang)]
    for prefix, icon in (("working_hours", "🕒"), ("contact_info", "☎️")):
        text = localized_config(config, prefix, lang)
        if text:
            sections.append(f"{icon} {text}")
    if not custom and len(sections) > 1:
        sections[0] = sections[0].replace("*", "").replace("_", "")
    await send_menu_text(
        update.message, "\n\n".join(sections), lang,
        parse_mode="Markdown" if not custom and len(sections) == 1 else None,
    )



async def change_language(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Trigger language change."""
    return await start_command(update, context)


# ============================================================================
# Cancel Handler
# ============================================================================

async def cancel_conversation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Cancel current conversation."""
    lang = await get_user_lang(update.effective_user.id)

    async with get_session_factory()() as session:
        await UserService.get_or_create_user(session, update.effective_user.id)
        # Keep draft for resume later
        if context.user_data.get("title"):
            await save_draft(update.effective_user.id, context.user_data)

    context.user_data.clear()
    await update.message.reply_text(
        t("cancelled", lang),
        reply_markup=main_menu_keyboard(lang),
    )
    return ConversationHandler.END


# ============================================================================
# Draft Helper
# ============================================================================

async def save_draft(telegram_id: int, data: dict) -> None:
    """Save current conversation data as draft."""
    # Filter out non-serializable items
    draft_data = {
        k: v for k, v in data.items()
        if k not in ("_editing",) and isinstance(v, (str, int, float, bool, list, dict, type(None)))
    }
    async with get_session_factory()() as session:
        user = await UserService.get_or_create_user(session, telegram_id)
        await DraftService.save_draft(session, user.id, draft_data)


# ============================================================================
# Error Handler
# ============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler. Logs error without PII, shows generic message."""
    logger.exception("Bot error occurred")

    if isinstance(update, Update) and update.effective_message:
        try:
            lang = "uz"
            if update.effective_user:
                lang = await get_user_lang(update.effective_user.id)
            await update.effective_message.reply_text(t("error_generic", lang))
        except Exception:
            pass


# ============================================================================
# Admin Authentication
# ============================================================================

from sqlalchemy import select

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user: return
    async with get_session_factory()() as session:
        from app.models import AdminUser, AdminRole, Assignment, Complaint, ComplaintStatus
        from sqlalchemy import func

        result = await session.execute(select(AdminUser).where(
            AdminUser.telegram_id == update.effective_user.id,
            AdminUser.is_active.is_(True),
        ))
        admin = result.scalar_one_or_none()

        if not admin:
            await update.message.reply_text("Sizda admin huquqi yo‘q.")
            return

        filters = [Complaint.archived_at.is_(None), Complaint.deleted_at.is_(None)]
        if admin.role in {AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value}:
            assignments = select(Assignment.complaint_id).where(
                Assignment.organization_id == admin.organization_id,
                Assignment.is_active.is_(True),
            )
            if admin.role == AdminRole.EXECUTOR.value:
                assignments = assignments.where(Assignment.assigned_to_id == admin.id)
            filters.append(Complaint.id.in_(assignments))

        async def count_status(value: str) -> int:
            return int((await session.execute(
                select(func.count(Complaint.id)).where(Complaint.status == value, *filters)
            )).scalar_one())

        new_count = await count_status(ComplaintStatus.SUBMITTED.value)
        in_prog_count = await count_status(ComplaintStatus.IN_PROGRESS.value)
        resolved_count = await count_status(ComplaintStatus.RESOLVED.value)

        text = (
            f"👑 *Admin Panel*\n\n"
            f"Yangi murojaatlar: {new_count}\n"
            f"Jarayondagi murojaatlar: {in_prog_count}\n"
            f"Hal qilingan murojaatlar: {resolved_count}\n\n"
            "To‘liq boshqaruv web panelda: murojaatlar, idoralar, yo‘nalishlar, adminlar va bot sozlamalari."
        )
        await update.message.reply_text(text, parse_mode="Markdown")

# ============================================================================
# Bot Application Builder
# ============================================================================

def create_bot_application() -> Application:
    """Build and configure the Telegram bot application."""
    settings = get_settings()

    app = Application.builder().token(settings.telegram_bot_token).build()

    # Start conversation (language + consent)
    start_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command),
            MessageHandler(MENU_BUTTON_FILTERS["btn_change_language"], start_command),
        ],
        states={
            SELECTING_LANG: [
                CallbackQueryHandler(handle_language_selection, pattern=r"^set_lang_"),
            ],
            CONSENT: [
                CallbackQueryHandler(handle_consent, pattern=r"^consent_"),
            ],
        },
        fallbacks=[CommandHandler("start", start_command)],
        name="start_conversation",
        persistent=False,
    )

    # Complaint submission conversation
    # Build entry patterns for both languages
    complaint_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                MENU_BUTTON_FILTERS["btn_new_complaint"],
                complaint_start,
            ),
        ],
        states={
            SELECTING_LANG: [
                CallbackQueryHandler(handle_language_selection, pattern=r"^set_lang_"),
            ],
            CONSENT: [
                CallbackQueryHandler(handle_consent, pattern=r"^consent_"),
            ],
            COMPLAINT_LANG: [
                CallbackQueryHandler(handle_complaint_lang),
            ],
            COMPLAINT_TYPE: [
                CallbackQueryHandler(handle_complaint_type, pattern=r"^ctype_"),
            ],
            ENTER_NAME: [
                MessageHandler(FORM_TEXT_FILTER, handle_name),
                CallbackQueryHandler(handle_title, pattern=r"^dup_"),
            ],
            ENTER_PHONE: [
                MessageHandler(PHONE_INPUT_FILTER, handle_phone),
            ],
            ENTER_BIRTH_DATE: [
                MessageHandler(FORM_TEXT_FILTER, handle_birth_date),
                CallbackQueryHandler(handle_birth_date, pattern=r"^skip_birth_date$"),
            ],
            ENTER_PASSPORT: [
                MessageHandler(FORM_TEXT_FILTER, handle_passport),
                CallbackQueryHandler(handle_passport, pattern=r"^skip_passport$"),
            ],
            SELECT_MFY: [
                CallbackQueryHandler(handle_mfy_selection, pattern=r"^mfy_"),
            ],
            ENTER_ADDRESS: [
                CallbackQueryHandler(handle_address, pattern=r"^address_skip$"),
                MessageHandler(FORM_TEXT_FILTER, handle_address),
            ],
            SELECT_CATEGORY: [
                CallbackQueryHandler(handle_category, pattern=r"^cat_"),
            ],
            ENTER_TITLE: [
                MessageHandler(FORM_TEXT_FILTER, handle_title),
                CallbackQueryHandler(handle_title, pattern=r"^dup_"),
            ],
            ENTER_DESCRIPTION: [
                MessageHandler(FORM_TEXT_FILTER, handle_description),
            ],
            ATTACHMENTS: [
                CallbackQueryHandler(handle_attachment, pattern=r"^(skip_attach|finish_attach)$"),
                MessageHandler(
                    filters.PHOTO | filters.Document.ALL | filters.VIDEO,
                    handle_attachment,
                ),
                MessageHandler(FORM_TEXT_FILTER, handle_attachment),
            ],
            PREVIEW: [
                CallbackQueryHandler(handle_preview_action),
            ],
            EDITING: [
                CallbackQueryHandler(handle_edit_choice),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CommandHandler("start", restart_complaint_with_start),
            MessageHandler(AnyConfiguredButtonFilter(), route_menu_during_complaint),
        ],
        name="complaint_conversation",
        persistent=False,
        allow_reentry=True,
    )

    additional_info_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(
            start_additional_info, pattern=r"^additional_info_\d+$"
        )],
        states={
            SELECTING_LANG: [CallbackQueryHandler(
                handle_language_selection, pattern=r"^set_lang_"
            )],
            CONSENT: [CallbackQueryHandler(
                handle_consent, pattern=r"^consent_"
            )],
            ADDITIONAL_INFO: [MessageHandler(
                FORM_TEXT_FILTER
                | filters.PHOTO
                | filters.Document.ALL
                | filters.VIDEO,
                receive_additional_info,
            )],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_additional_info),
            CommandHandler("start", restart_from_additional_info),
            MessageHandler(
                AnyConfiguredButtonFilter(), route_menu_during_additional_info
            ),
        ],
        name="additional_info_conversation",
        persistent=False,
    )

    # Register handlers
    app.add_handler(CommandHandler("admin_panel", admin_panel_command))
    app.add_handler(CallbackQueryHandler(
        handle_citizen_resolution,
        pattern=r"^citizen_(confirm|reopen)_\d+$",
    ))
    app.add_handler(additional_info_conv)
    app.add_handler(complaint_conv)
    app.add_handler(start_conv)

    # Menu handlers (outside conversations)
    app.add_handler(MessageHandler(
        MENU_BUTTON_FILTERS["btn_my_complaints"],
        my_complaints,
    ))
    app.add_handler(MessageHandler(
        MENU_BUTTON_FILTERS["btn_help"],
        help_command,
    ))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        quick_answer_handler,
    ))

    # Global error handler
    app.add_error_handler(error_handler)

    return app


async def run_bot() -> None:
    """Start the bot with published settings loaded before Telegram updates arrive."""
    app = create_bot_application()
    logger.info("Starting Telegram bot in polling mode...")
    worker_task: asyncio.Task | None = None
    initialized = False
    try:
        # Application.initialize performs the Telegram getMe check. Load the active
        # database revision before polling so the first update cannot see defaults.
        await app.initialize()
        initialized = True
        await refresh_runtime_bot_config()
        await app.start()

        def polling_error(error: Exception) -> None:
            logger.error(
                "Telegram polling request failed",
                extra={"error_type": type(error).__name__},
            )

        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
            error_callback=polling_error,
        )
        worker_task = asyncio.create_task(notification_worker(app.bot))
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        if worker_task:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        if app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        if initialized:
            await app.shutdown()
