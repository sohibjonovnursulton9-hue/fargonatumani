"""
Telegram bot for citizen complaint submission.
Uses python-telegram-bot v20+ async ConversationHandler.

All user-facing text comes from app.i18n module.
PII is never logged. Drafts saved at each step.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
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
from app.database import async_session_factory
from app.i18n import get_status_label, get_type_label, t
from app.logging_config import get_logger
from app.models import (
    ComplaintLanguage,
    ComplaintStatus,
    ComplaintType,
)
from app.services import (
    CatalogService,
    ComplaintService,
    DraftService,
    RateLimitService,
    UserService,
)

logger = get_logger(__name__)

# Conversation states
(
    SELECTING_LANG,
    CONSENT,
    COMPLAINT_LANG,
    COMPLAINT_TYPE,
    ENTER_NAME,
    ENTER_PHONE,
    SELECT_MFY,
    ENTER_ADDRESS,
    SELECT_CATEGORY,
    ENTER_TITLE,
    ENTER_DESCRIPTION,
    ATTACHMENTS,
    PREVIEW,
    EDITING,
    CITIZEN_CONFIRM,
) = range(15)

CONSENT_VERSION = "1.0-draft"

PHONE_REGEX = re.compile(r"^\+998\d{9}$")


# ============================================================================
# Helpers
# ============================================================================

async def get_user_lang(telegram_id: int) -> str:
    """Get user's interface language from DB."""
    async with async_session_factory() as session:
        user = await UserService.get_or_create_user(session, telegram_id)
        await session.commit()
        return user.interface_language


def main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Build main menu reply keyboard."""
    return ReplyKeyboardMarkup(
        [
            [t("btn_new_complaint", lang)],
            [t("btn_my_complaints", lang), t("btn_help", lang)],
            [t("btn_change_language", lang)],
        ],
        resize_keyboard=True,
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
            t("welcome", "uz") + "\n\n" + t("welcome", "ru"),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    return SELECTING_LANG


async def handle_language_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Process interface language selection."""
    query = update.callback_query
    await query.answer()

    lang = query.data.split("_")[-1]  # "uz" or "ru"

    async with async_session_factory() as session:
        user = await UserService.get_or_create_user(
            session, query.from_user.id, query.from_user.username, lang
        )
        await UserService.update_language(session, user.id, lang)

        # Check consent
        has_consent = await UserService.has_valid_consent(
            session, user.id, CONSENT_VERSION
        )

    context.user_data["lang"] = lang

    if has_consent:
        await query.edit_message_text(
            t("language_selected", lang) + "\n\n" + t("main_menu", lang),
            parse_mode="Markdown",
        )
        await query.message.reply_text(
            t("main_menu", lang),
            reply_markup=main_menu_keyboard(lang),
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    else:
        # Show consent
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    t("consent_accept_btn", lang), callback_data="consent_accept"
                ),
                InlineKeyboardButton(
                    t("consent_decline_btn", lang), callback_data="consent_decline"
                ),
            ]
        ])
        await query.edit_message_text(
            t("consent_text", lang),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return CONSENT


async def handle_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle consent acceptance or decline."""
    query = update.callback_query
    await query.answer()

    lang = context.user_data.get("lang", "uz")
    accepted = query.data == "consent_accept"

    async with async_session_factory() as session:
        user = await UserService.get_or_create_user(session, query.from_user.id)
        consent_text = t("consent_text", lang)
        await UserService.record_consent(
            session, user.id, CONSENT_VERSION, consent_text, accepted
        )
        await session.commit()

    if accepted:
        await query.edit_message_text(
            t("consent_accepted", lang), parse_mode="Markdown"
        )
        await query.message.reply_text(
            t("main_menu", lang),
            reply_markup=main_menu_keyboard(lang),
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    else:
        await query.edit_message_text(
            t("consent_declined", lang), parse_mode="Markdown"
        )
        return ConversationHandler.END


# ============================================================================
# Complaint Submission ConversationHandler
# ============================================================================

async def complaint_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Entry point for complaint submission."""
    lang = await get_user_lang(update.effective_user.id)
    context.user_data["lang"] = lang
    context.user_data["attachments"] = []

    # Check rate limit
    async with async_session_factory() as session:
        allowed, count = await RateLimitService.check_rate_limit(
            session, update.effective_user.id
        )

    if not allowed:
        settings = get_settings()
        await update.message.reply_text(
            t("rate_limit_exceeded", lang, max=settings.max_complaints_per_day),
            reply_markup=main_menu_keyboard(lang),
        )
        return ConversationHandler.END

    # Check for existing draft
    async with async_session_factory() as session:
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
        # Load draft data
        async with async_session_factory() as session:
            user = await UserService.get_or_create_user(session, query.from_user.id)
            draft = await DraftService.get_draft(session, user.id)
        if draft:
            try:
                data = json.loads(draft.draft_data)
                context.user_data.update(data)
            except (json.JSONDecodeError, TypeError):
                pass
        return await show_complaint_lang_selection(update, context)

    if query.data == "draft_new":
        async with async_session_factory() as session:
            user = await UserService.get_or_create_user(session, query.from_user.id)
            await DraftService.delete_draft(session, user.id)
        context.user_data["attachments"] = []
        return await show_complaint_lang_selection(update, context)

    # Complaint language selected
    clang_map = {
        "clang_uz_latin": ComplaintLanguage.UZ_LATIN.value,
        "clang_uz_cyrillic": ComplaintLanguage.UZ_CYRILLIC.value,
        "clang_ru": ComplaintLanguage.RU.value,
    }
    context.user_data["complaint_language"] = clang_map.get(query.data, ComplaintLanguage.UZ_LATIN.value)

    # Show complaint type selection
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_ariza", lang), callback_data="ctype_ariza")],
        [InlineKeyboardButton(t("btn_shikoyat", lang), callback_data="ctype_shikoyat")],
        [InlineKeyboardButton(t("btn_taklif", lang), callback_data="ctype_taklif")],
    ])
    await query.edit_message_text(
        t("select_complaint_type", lang), reply_markup=keyboard, parse_mode="Markdown"
    )
    return COMPLAINT_TYPE


async def handle_complaint_type(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle complaint type selection."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "uz")

    type_map = {
        "ctype_ariza": ComplaintType.ARIZA.value,
        "ctype_shikoyat": ComplaintType.SHIKOYAT.value,
        "ctype_taklif": ComplaintType.TAKLIF.value,
    }
    context.user_data["complaint_type"] = type_map.get(query.data, ComplaintType.ARIZA.value)

    await query.edit_message_text(t("enter_full_name", lang), parse_mode="Markdown")
    return ENTER_NAME


async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle full name input."""
    lang = context.user_data.get("lang", "uz")
    name = update.message.text.strip()

    if len(name.split()) < 2:
        await update.message.reply_text(t("invalid_full_name", lang))
        return ENTER_NAME

    context.user_data["full_name"] = name

    # Save draft
    await save_draft(update.effective_user.id, context.user_data)

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(t("btn_share_contact", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        t("enter_phone", lang), reply_markup=keyboard, parse_mode="Markdown"
    )
    return ENTER_PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle phone number input (manual or contact share)."""
    lang = context.user_data.get("lang", "uz")

    if update.message.contact:
        phone = update.message.contact.phone_number
        if not phone.startswith("+"):
            phone = "+" + phone
    else:
        phone = update.message.text.strip()

    if not PHONE_REGEX.match(phone):
        await update.message.reply_text(t("invalid_phone", lang))
        return ENTER_PHONE

    context.user_data["phone_number"] = phone
    await save_draft(update.effective_user.id, context.user_data)

    # Show MFY selection
    async with async_session_factory() as session:
        mfy_areas = await CatalogService.get_active_mfy_areas(session)

    buttons = []
    for mfy in mfy_areas:
        name = mfy.name_uz if lang == "uz" else mfy.name_ru
        buttons.append([InlineKeyboardButton(name, callback_data=f"mfy_{mfy.id}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        t("select_mfy", lang),
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    # Remove reply keyboard
    return SELECT_MFY


async def handle_mfy_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle MFY selection."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "uz")

    mfy_id = int(query.data.split("_")[1])
    context.user_data["mfy_area_id"] = mfy_id
    await save_draft(query.from_user.id, context.user_data)

    await query.edit_message_text(t("enter_address_detail", lang), parse_mode="Markdown")
    return ENTER_ADDRESS


async def handle_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle address input."""
    lang = context.user_data.get("lang", "uz")
    context.user_data["address_detail"] = update.message.text.strip()
    await save_draft(update.effective_user.id, context.user_data)

    # Check for duplicate hint
    async with async_session_factory() as session:
        await UserService.get_or_create_user(session, update.effective_user.id)
        categories = await CatalogService.get_active_categories(session)

    buttons = []
    for cat in categories:
        if lang == "ru":
            name = cat.name_ru
        else:
            name = cat.name_uz
        buttons.append([InlineKeyboardButton(name, callback_data=f"cat_{cat.id}")])

    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        t("select_category", lang),
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return SELECT_CATEGORY


async def handle_category(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle category selection and check for duplicates."""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "uz")

    category_id = int(query.data.split("_")[1])
    context.user_data["category_id"] = category_id
    await save_draft(query.from_user.id, context.user_data)

    # Check duplicate hint (never auto-reject)
    async with async_session_factory() as session:
        user = await UserService.get_or_create_user(session, query.from_user.id)
        duplicate = await ComplaintService.check_duplicate_hint(
            session, user.id, category_id
        )

        # Check for emergency category
        category = await CatalogService.get_category_by_id(session, category_id)
        if category and category.is_emergency:
            guidance = category.emergency_guidance_uz if lang == "uz" else category.emergency_guidance_ru
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
              title=duplicate.title,
              status=dup_status),
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return ENTER_TITLE  # Handle dup choice in title state

    await query.edit_message_text(t("enter_title", lang), parse_mode="Markdown")
    return ENTER_TITLE


async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle title input or duplicate choice."""
    lang = context.user_data.get("lang", "uz")

    # Handle callback from duplicate warning
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data == "dup_new":
            await query.edit_message_text(t("enter_title", lang), parse_mode="Markdown")
            return ENTER_TITLE
        elif query.data.startswith("dup_view_"):
            # Show existing complaint - redirect to my complaints
            await query.edit_message_text(
                t("main_menu", lang),
                parse_mode="Markdown",
            )
            return ConversationHandler.END

    title = update.message.text.strip()
    if len(title) > 500:
        await update.message.reply_text("❌ Sarlavha 500 belgidan oshmasligi kerak / Заголовок не должен превышать 500 символов")
        return ENTER_TITLE

    context.user_data["title"] = title
    await save_draft(update.effective_user.id, context.user_data)

    await update.message.reply_text(t("enter_description", lang), parse_mode="Markdown")
    return ENTER_DESCRIPTION


async def handle_description(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle complaint description."""
    lang = context.user_data.get("lang", "uz")
    context.user_data["complaint_text"] = update.message.text.strip()
    await save_draft(update.effective_user.id, context.user_data)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_skip_attachments", lang), callback_data="skip_attach")]
    ])
    await update.message.reply_text(
        t("ask_attachments", lang),
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return ATTACHMENTS


async def handle_attachment(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle file attachment or skip."""
    lang = context.user_data.get("lang", "uz")

    if update.callback_query and update.callback_query.data == "skip_attach":
        await update.callback_query.answer()
        return await show_preview(update, context)

    # Handle actual file
    if update.message:
        file_info = None
        file_type = None

        if update.message.photo:
            file_info = update.message.photo[-1]  # Largest size
            file_type = "photo"
        elif update.message.document:
            file_info = update.message.document
            file_type = "document"
        elif update.message.video:
            file_info = update.message.video
            file_type = "video"

        if file_info:
            # Check file size
            settings = get_settings()
            file_size = getattr(file_info, "file_size", 0) or 0
            if file_size > settings.max_file_size:
                await update.message.reply_text(t("attachment_invalid", lang))
                return ATTACHMENTS

            # Check extension for documents
            if file_type == "document" and hasattr(file_info, "file_name"):
                ext = (file_info.file_name or "").rsplit(".", 1)[-1].lower()
                if ext not in settings.allowed_extensions_list:
                    await update.message.reply_text(t("attachment_invalid", lang))
                    return ATTACHMENTS

            attachments = context.user_data.get("attachments", [])
            attachments.append({
                "file_id": file_info.file_id,
                "file_type": file_type,
                "file_name": getattr(file_info, "file_name", None),
                "file_size": file_size,
                "mime_type": getattr(file_info, "mime_type", None),
            })
            context.user_data["attachments"] = attachments

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(t("btn_skip_attachments", lang), callback_data="skip_attach")]
            ])
            await update.message.reply_text(
                t("attachment_saved", lang),
                reply_markup=keyboard,
            )
            return ATTACHMENTS

    return ATTACHMENTS


async def show_preview(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Show complaint preview for user confirmation."""
    lang = context.user_data.get("lang", "uz")
    data = context.user_data

    # Get category and MFY names
    cat_name = ""
    mfy_name = ""
    async with async_session_factory() as session:
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
    preview += t("preview_type", lang, type=type_label)
    preview += t("preview_name", lang, name=data.get("full_name", "—"))
    preview += t("preview_phone", lang, phone=data.get("phone_number", "—"))
    preview += t("preview_address", lang, mfy=mfy_name, address=data.get("address_detail", "—"))
    preview += t("preview_category", lang, category=cat_name)
    preview += t("preview_title", lang, title=data.get("title", "—"))
    preview += t("preview_text", lang, text=data.get("complaint_text", "—")[:200])
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
    lang = context.user_data.get("lang", "uz")

    if query.data == "cancel_complaint":
        async with async_session_factory() as session:
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
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("btn_edit_name", lang), callback_data="edit_name")],
            [InlineKeyboardButton(t("btn_edit_phone", lang), callback_data="edit_phone")],
            [InlineKeyboardButton(t("btn_edit_address", lang), callback_data="edit_address")],
            [InlineKeyboardButton(t("btn_edit_category", lang), callback_data="edit_category")],
            [InlineKeyboardButton(t("btn_edit_title", lang), callback_data="edit_title")],
            [InlineKeyboardButton(t("btn_edit_text", lang), callback_data="edit_text")],
            [InlineKeyboardButton(t("btn_done_editing", lang), callback_data="edit_done")],
        ])
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
    lang = context.user_data.get("lang", "uz")

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
        return state

    if query.data == "edit_address":
        context.user_data["_editing"] = True
        await query.edit_message_text(t("enter_address_detail", lang), parse_mode="Markdown")
        return ENTER_ADDRESS

    if query.data == "edit_category":
        context.user_data["_editing"] = True
        async with async_session_factory() as session:
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
    """Submit the complaint to database."""
    query = update.callback_query
    lang = context.user_data.get("lang", "uz")
    data = context.user_data

    try:
        async with async_session_factory() as session:
            user = await UserService.get_or_create_user(session, query.from_user.id)

            complaint = await ComplaintService.create_complaint(
                session=session,
                user_id=user.id,
                complaint_type=data.get("complaint_type", ComplaintType.ARIZA.value),
                complaint_language=data.get("complaint_language", ComplaintLanguage.UZ_LATIN.value),
                category_id=data["category_id"],
                full_name=data["full_name"],
                phone_number=data["phone_number"],
                title=data["title"],
                complaint_text=data["complaint_text"],
                mfy_area_id=data.get("mfy_area_id"),
                address_detail=data.get("address_detail"),
            )

            # Save attachments
            from app.models import Attachment
            for att in data.get("attachments", []):
                attachment = Attachment(
                    complaint_id=complaint.id,
                    file_type=att["file_type"],
                    file_name=att.get("file_name"),
                    file_size=att.get("file_size"),
                    telegram_file_id=att["file_id"],
                    mime_type=att.get("mime_type"),
                    uploaded_by="citizen",
                )
                session.add(attachment)

            # Increment rate limit
            await RateLimitService.increment_rate_limit(session, query.from_user.id)

            # Delete draft
            await DraftService.delete_draft(session, user.id)

            await session.commit()

            # Get category name for receipt
            cat = await CatalogService.get_category_by_id(session, data["category_id"])
            cat_name = cat.name_ru if lang == "ru" else cat.name_uz if cat else "—"

        type_label = get_type_label(complaint.complaint_type, lang)
        date_str = format_tashkent_time(complaint.submitted_at)

        receipt = t(
            "complaint_submitted", lang,
            tracking_id=complaint.tracking_id,
            date=date_str,
            type=type_label,
            category=cat_name,
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
        await query.edit_message_text(t("error_generic", lang))
        return ConversationHandler.END


# ============================================================================
# My Complaints
# ============================================================================

async def my_complaints(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show user's complaints list."""
    lang = await get_user_lang(update.effective_user.id)

    async with async_session_factory() as session:
        user = await UserService.get_or_create_user(session, update.effective_user.id)
        complaints = await ComplaintService.get_user_complaints(session, user.id)

    if not complaints:
        await update.message.reply_text(t("no_complaints", lang))
        return

    text = t("complaints_list_header", lang)
    for c in complaints[:10]:  # Show last 10
        status_label = get_status_label(c.status, lang)
        date_str = format_tashkent_time(c.submitted_at)
        text += t(
            "complaint_list_item", lang,
            tracking_id=c.tracking_id,
            title=c.title[:50],
            status=status_label,
            date=date_str,
        )

        # Show confirmation buttons if pending
        if c.status == ComplaintStatus.CITIZEN_CONFIRMATION_PENDING.value:
            pass  # Handled via notification flow

    await update.message.reply_text(text, parse_mode="Markdown")


# ============================================================================
# Help & Language Change
# ============================================================================

async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show help text."""
    lang = await get_user_lang(update.effective_user.id)
    await update.message.reply_text(t("help_text", lang), parse_mode="Markdown")


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

    async with async_session_factory() as session:
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
    async with async_session_factory() as session:
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
# Bot Application Builder
# ============================================================================

def create_bot_application() -> Application:
    """Build and configure the Telegram bot application."""
    settings = get_settings()

    app = Application.builder().token(settings.telegram_bot_token).build()

    # Start conversation (language + consent)
    start_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
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
                filters.Regex(r"^(📝 Murojaat yuborish|📝 Подать обращение)$"),
                complaint_start,
            ),
        ],
        states={
            COMPLAINT_LANG: [
                CallbackQueryHandler(handle_complaint_lang),
            ],
            COMPLAINT_TYPE: [
                CallbackQueryHandler(handle_complaint_type, pattern=r"^ctype_"),
            ],
            ENTER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name),
                CallbackQueryHandler(handle_title, pattern=r"^dup_"),
            ],
            ENTER_PHONE: [
                MessageHandler(
                    (filters.TEXT & ~filters.COMMAND) | filters.CONTACT,
                    handle_phone,
                ),
            ],
            SELECT_MFY: [
                CallbackQueryHandler(handle_mfy_selection, pattern=r"^mfy_"),
            ],
            ENTER_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address),
            ],
            SELECT_CATEGORY: [
                CallbackQueryHandler(handle_category, pattern=r"^cat_"),
            ],
            ENTER_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title),
                CallbackQueryHandler(handle_title, pattern=r"^dup_"),
            ],
            ENTER_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description),
            ],
            ATTACHMENTS: [
                CallbackQueryHandler(handle_attachment, pattern=r"^skip_attach$"),
                MessageHandler(
                    filters.PHOTO | filters.Document.ALL | filters.VIDEO,
                    handle_attachment,
                ),
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
            CommandHandler("start", start_command),
        ],
        name="complaint_conversation",
        persistent=False,
    )

    # Register handlers
    app.add_handler(start_conv)
    app.add_handler(complaint_conv)

    # Menu handlers (outside conversations)
    app.add_handler(MessageHandler(
        filters.Regex(r"^(📂 Murojaatlarim|📂 Мои обращения)$"),
        my_complaints,
    ))
    app.add_handler(MessageHandler(
        filters.Regex(r"^(❓ Yordam / Aloqa|❓ Помощь / Контакты)$"),
        help_command,
    ))
    app.add_handler(MessageHandler(
        filters.Regex(r"^(🌐 Tilni o'zgartirish|🌐 Сменить язык)$"),
        change_language,
    ))

    # Global error handler
    app.add_error_handler(error_handler)

    return app


async def run_bot() -> None:
    """Start the bot in polling mode (development)."""
    app = create_bot_application()
    logger.info("Starting Telegram bot in polling mode...")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)
