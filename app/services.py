"""
Core business logic services for the complaints management system.

Services handle:
- Complaint lifecycle (create, submit, transition states)
- Deadline management
- Rate limiting
- Duplicate detection (hint-only, never auto-reject)
- Audit trail
- User management and consent
"""

from __future__ import annotations

import hashlib
import json
import asyncio
import math
import statistics
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.staff_notifications import queue_supervisor_notice, queue_assignment_notice
from app.config import get_settings
from app.logging_config import get_logger
from app.i18n import get_status_label
from app.models import (
    VALID_TRANSITIONS,
    AdminUser,
    Assignment,
    AuditAction,
    AuditEvent,
    Category,
    CategoryOrganization,
    Complaint,
    ComplaintDraft,
    ComplaintStatus,
    ComplaintType,
    ConsentRecord,
    Deadline,
    DeadlineExtension,
    MFYArea,
    Notification,
    Organization,
    RateLimit,
    Response,
    StatusEvent,
    User,
    generate_tracking_id,
    utcnow,
)

logger = get_logger(__name__)
_rate_limit_locks: dict[tuple[int, str], asyncio.Lock] = {}


# ============================================================================
# User Service
# ============================================================================

class UserService:
    """Manage citizen users and consent."""

    @staticmethod
    async def get_or_create_user(
        session: AsyncSession,
        telegram_id: int,
        username: str | None = None,
        language: str = "uz",
    ) -> User:
        """Get existing user or create new one."""
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=telegram_id,
                telegram_username=username,
                interface_language=language,
            )
            session.add(user)
            await session.flush()
            await queue_supervisor_notice(
                session, "citizen_registered", "👤 Botga yangi foydalanuvchi qo‘shildi."
            )
            logger.info(
                "New user created",
                user_id=user.id,
                telegram_id=telegram_id,
            )
        return user

    @staticmethod
    async def update_language(
        session: AsyncSession, user_id: int, language: str
    ) -> None:
        """Update user's interface language."""
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(interface_language=language)
        )
        await session.commit()

    @staticmethod
    async def record_consent(
        session: AsyncSession,
        user_id: int,
        consent_version: str,
        consent_text: str,
        accepted: bool,
    ) -> ConsentRecord:
        """Record user's consent decision. Immutable record."""
        text_hash = hashlib.sha256(consent_text.encode()).hexdigest()
        record = ConsentRecord(
            user_id=user_id,
            consent_version=consent_version,
            consent_text_hash=text_hash,
            accepted=accepted,
        )
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    async def has_valid_consent(
        session: AsyncSession, user_id: int, current_version: str
    ) -> bool:
        """Check the latest decision for the current consent version."""
        stmt = (
            select(ConsentRecord)
            .where(
                and_(
                    ConsentRecord.user_id == user_id,
                    ConsentRecord.consent_version == current_version,
                )
            )
            .order_by(ConsentRecord.accepted_at.desc(), ConsentRecord.id.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        latest_decision = result.scalar_one_or_none()
        return bool(latest_decision and latest_decision.accepted)


# ============================================================================
# Rate Limit Service
# ============================================================================

class RateLimitService:
    """Enforce per-user rate limits."""

    @staticmethod
    async def check_rate_limit(
        session: AsyncSession,
        telegram_id: int,
        action: str = "complaint",
        max_per_day: int | None = None,
    ) -> tuple[bool, int]:
        """
        Check if user is within rate limit.
        Returns (is_allowed, current_count).
        """
        limit = max_per_day if max_per_day is not None else get_settings().max_complaints_per_day
        count = await RateLimitService._count_recent(session, telegram_id, action)
        return count < limit, count

    @staticmethod
    async def _count_recent(
        session: AsyncSession, telegram_id: int, action: str
    ) -> int:
        cutoff = utcnow() - timedelta(hours=24)
        stmt = select(func.coalesce(func.sum(RateLimit.count), 0)).where(
            and_(
                RateLimit.telegram_id == telegram_id,
                RateLimit.action == action,
                RateLimit.window_start > cutoff,
            )
        )
        return int((await session.execute(stmt)).scalar_one())

    @staticmethod
    @asynccontextmanager
    async def reserve_complaint_slot(
        session: AsyncSession,
        telegram_id: int,
        action: str = "complaint",
        max_per_day: int | None = None,
    ) -> AsyncIterator[tuple[bool, int]]:
        """Reserve one rolling 24-hour slot until the caller commits or rolls back."""
        dialect = session.get_bind().dialect.name
        lock = None
        if dialect == "sqlite":
            lock = _rate_limit_locks.setdefault((telegram_id, action), asyncio.Lock())
            await lock.acquire()

        try:
            if dialect == "postgresql":
                lock_key = int.from_bytes(
                    hashlib.sha256(f"ftmt:{action}:{telegram_id}".encode()).digest()[:8],
                    byteorder="big",
                    signed=True,
                )
                await session.execute(select(func.pg_advisory_xact_lock(lock_key)))

            count = await RateLimitService._count_recent(session, telegram_id, action)
            limit = max_per_day if max_per_day is not None else get_settings().max_complaints_per_day
            if count >= limit:
                yield False, count
                return

            now = utcnow()
            stmt = select(RateLimit).where(
                and_(
                    RateLimit.telegram_id == telegram_id,
                    RateLimit.action == action,
                    RateLimit.window_start == now,
                )
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                existing.count += 1
            else:
                session.add(
                    RateLimit(
                        telegram_id=telegram_id,
                        action=action,
                        window_start=now,
                        count=1,
                    )
                )
            await session.flush()
            yield True, count + 1
        finally:
            if lock is not None and lock.locked():
                lock.release()

    @staticmethod
    async def increment_rate_limit(
        session: AsyncSession,
        telegram_id: int,
        action: str = "complaint",
    ) -> None:
        """Increment rate limit counter."""
        now = utcnow()
        stmt = select(RateLimit).where(
            and_(
                RateLimit.telegram_id == telegram_id,
                RateLimit.action == action,
                RateLimit.window_start == now,
            )
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.count += 1
        else:
            session.add(
                RateLimit(
                    telegram_id=telegram_id,
                    action=action,
                    window_start=now,
                    count=1,
                )
            )
        await session.flush()


# ============================================================================
# Complaint Service
# ============================================================================

class ComplaintService:
    """Core complaint lifecycle management."""

    @staticmethod
    async def create_complaint(
        session: AsyncSession,
        user_id: int,
        complaint_type: str,
        complaint_language: str,
        category_id: int,
        full_name: str,
        phone_number: str,
        title: str,
        complaint_text: str,
        mfy_area_id: int | None = None,
        address_detail: str | None = None,
        birth_date: str | None = None,
        passport_data: str | None = None,
        deadline_days: int | None = None,
        commit: bool = True,
    ) -> Complaint:
        """
        Create and submit a new complaint in a single transaction.
        Generates tracking ID and creates initial status event.
        """
        # Generate unique tracking ID (retry on collision)
        tracking_id = generate_tracking_id()
        # Simple collision check
        existing = await session.execute(
            select(Complaint.id).where(Complaint.tracking_id == tracking_id)
        )
        while existing.scalar_one_or_none() is not None:
            tracking_id = generate_tracking_id()
            existing = await session.execute(
                select(Complaint.id).where(Complaint.tracking_id == tracking_id)
            )

        now = utcnow()
        complaint = Complaint(
            tracking_id=tracking_id,
            user_id=user_id,
            complaint_type=complaint_type,
            complaint_language=complaint_language,
            category_id=category_id,
            full_name=full_name,
            phone_number=phone_number,
            phone_verified=False,  # OTP not implemented
            mfy_area_id=mfy_area_id,
            address_detail=address_detail,
            birth_date=birth_date,
            passport_data=passport_data,
            title=title,
            complaint_text=complaint_text,
            status=ComplaintStatus.SUBMITTED.value,
            submitted_at=now,
        )
        session.add(complaint)
        await session.flush()

        # Create initial status event
        status_event = StatusEvent(
            complaint_id=complaint.id,
            from_status=None,
            to_status=ComplaintStatus.SUBMITTED.value,
            reason="Fuqaro tomonidan yuborildi / Submitted by citizen",
            actor_type="citizen",
            actor_id=str(user_id),
        )
        session.add(status_event)

        # Create deadline
        settings = get_settings()
        configured_deadline_days = {
            ComplaintType.ARIZA.value: settings.default_ariza_deadline_days,
            ComplaintType.SHIKOYAT.value: settings.default_shikoyat_deadline_days,
            ComplaintType.TAKLIF.value: settings.default_taklif_deadline_days,
        }.get(complaint_type, 15)
        if deadline_days is None:
            deadline_days = configured_deadline_days
        elif type(deadline_days) is not int or not 1 <= deadline_days <= 365:
            raise ValueError("deadline_days must be an integer from 1 to 365")

        deadline = Deadline(
            complaint_id=complaint.id,
            original_deadline=now + timedelta(days=deadline_days),
            current_deadline=now + timedelta(days=deadline_days),
        )
        session.add(deadline)

        # Create audit event
        audit = AuditEvent(
            action=AuditAction.COMPLAINT_CREATED.value,
            entity_type="complaint",
            entity_id=str(complaint.id),
            actor_type="citizen",
            actor_id=str(user_id),
            new_value=json.dumps({
                "tracking_id": tracking_id,
                "type": complaint_type,
                "category_id": category_id,
                "status": ComplaintStatus.SUBMITTED.value,
            }),
        )
        session.add(audit)

        await queue_supervisor_notice(
            session, "complaint_received",
            f"🔔 Yangi murojaat tushdi.\nTracking raqami: {tracking_id}\n"
            "Admin panelida ko‘rib chiqing va mas’ul idoraga biriktiring.",
            complaint_id=complaint.id,
        )
        if commit:
            await session.commit()
        else:
            await session.flush()

        logger.info(
            "Complaint created",
            complaint_id=complaint.id,
            tracking_id=tracking_id,
            complaint_type=complaint_type,
            category_id=category_id,
            status=ComplaintStatus.SUBMITTED.value,
        )

        return complaint

    @staticmethod
    async def transition_status(
        session: AsyncSession,
        complaint_id: int,
        new_status: ComplaintStatus,
        actor_type: str,
        actor_id: str | None = None,
        reason: str | None = None,
        notify_citizen: bool = True,
        commit: bool = True,
    ) -> Complaint:
        """
        Transition complaint to a new status with validation.
        Creates immutable status event and audit record.
        Raises ValueError if transition is invalid.
        """
        stmt = select(Complaint).where(Complaint.id == complaint_id)
        result = await session.execute(stmt)
        complaint = result.scalar_one_or_none()

        if complaint is None:
            raise ValueError(f"Complaint {complaint_id} not found")
        if complaint.deleted_at is not None:
            raise ValueError("Deleted complaints cannot be changed")
        if complaint.archived_at is not None:
            raise ValueError("Archived complaints cannot be changed")

        current_status = ComplaintStatus(complaint.status)
        valid_next = VALID_TRANSITIONS.get(current_status, set())

        if new_status not in valid_next:
            raise ValueError(
                f"Invalid transition: {current_status.value} → {new_status.value}. "
                f"Valid: {[s.value for s in valid_next]}"
            )

        old_status = complaint.status
        complaint.status = new_status.value

        # Update resolved timestamp
        if new_status == ComplaintStatus.RESOLVED:
            complaint.resolved_at = utcnow()

        # Create status event (immutable)
        event = StatusEvent(
            complaint_id=complaint_id,
            from_status=old_status,
            to_status=new_status.value,
            reason=reason,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        session.add(event)

        # Audit
        audit = AuditEvent(
            action=AuditAction.STATUS_CHANGED.value,
            entity_type="complaint",
            entity_id=str(complaint_id),
            actor_type=actor_type,
            actor_id=actor_id,
            old_value=json.dumps({"status": old_status}),
            new_value=json.dumps({"status": new_status.value, "reason": reason}),
        )
        session.add(audit)

        user = await session.get(User, complaint.user_id) if notify_citizen else None
        if user and user.telegram_id:
            language = user.interface_language or "uz"
            label = get_status_label(new_status.value, language)
            is_confirmation = new_status == ComplaintStatus.CITIZEN_CONFIRMATION_PENDING
            if language == "ru":
                message = (
                    f"Обращение {complaint.tracking_id} ждёт вашего подтверждения: "
                    "вопрос решён или его нужно пересмотреть?"
                    if is_confirmation
                    else f"Статус обращения {complaint.tracking_id} изменён: {label}"
                )
            else:
                message = (
                    f"{complaint.tracking_id} raqamli murojaat bo‘yicha tasdiqingiz kerak: "
                    "masala hal bo‘ldimi yoki qayta ko‘rib chiqilsinmi?"
                    if is_confirmation
                    else f"{complaint.tracking_id} raqamli murojaat holati o‘zgardi: {label}"
                )
            session.add(Notification(
                complaint_id=complaint_id,
                recipient_telegram_id=user.telegram_id,
                notification_type="citizen_confirmation" if is_confirmation else "status_update",
                message_text=message,
                language=language,
            ))

        if commit:
            await session.commit()

        logger.info(
            "Complaint status changed",
            complaint_id=complaint_id,
            from_status=old_status,
            to_status=new_status.value,
        )

        return complaint

    @staticmethod
    async def get_complaint_by_tracking_id(
        session: AsyncSession, tracking_id: str
    ) -> Complaint | None:
        """Get complaint by citizen-facing tracking ID."""
        stmt = select(Complaint).where(
            Complaint.tracking_id == tracking_id, Complaint.deleted_at.is_(None)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_complaints(
        session: AsyncSession, user_id: int
    ) -> Sequence[Complaint]:
        """Get all complaints for a user, newest first."""
        stmt = (
            select(Complaint)
            .where(
                Complaint.user_id == user_id,
                Complaint.archived_at.is_(None),
                Complaint.deleted_at.is_(None),
            )
            .order_by(Complaint.created_at.desc())
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def confirm_resolved(
        session: AsyncSession, complaint_id: int, user_id: int
    ) -> Complaint:
        """Citizen confirms complaint is resolved."""
        complaint = await ComplaintService.transition_status(
            session=session,
            complaint_id=complaint_id,
            new_status=ComplaintStatus.RESOLVED,
            actor_type="citizen",
            actor_id=str(user_id),
            reason="Fuqaro masala hal bo'lganini tasdiqladi",
        )
        complaint.citizen_confirmed_at = utcnow()
        await session.commit()
        return complaint

    @staticmethod
    async def reopen_complaint(
        session: AsyncSession,
        complaint_id: int,
        user_id: int,
        reason: str,
    ) -> Complaint:
        """Citizen reports complaint not resolved, triggers reopen."""
        return await ComplaintService.transition_status(
            session=session,
            complaint_id=complaint_id,
            new_status=ComplaintStatus.REOPENED,
            actor_type="citizen",
            actor_id=str(user_id),
            reason=reason,
        )

    @staticmethod
    async def check_duplicate_hint(
        session: AsyncSession,
        user_id: int,
        category_id: int,
    ) -> Complaint | None:
        """
        Check for potential duplicate complaint (hint only, never auto-reject).
        Returns most recent open complaint in same category by same user, if exists.
        """
        from app.models import TERMINAL_STATUSES

        terminal_values = [s.value for s in TERMINAL_STATUSES]
        stmt = (
            select(Complaint)
            .where(
                and_(
                    Complaint.user_id == user_id,
                    Complaint.category_id == category_id,
                    Complaint.archived_at.is_(None),
                    Complaint.deleted_at.is_(None),
                    ~Complaint.status.in_(terminal_values),
                )
            )
            .order_by(Complaint.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


# ============================================================================
# Draft Service
# ============================================================================

class DraftService:
    """Manage complaint drafts for resume-after-timeout."""

    @staticmethod
    async def save_draft(
        session: AsyncSession,
        user_id: int,
        draft_data: dict[str, Any],
        conversation_state: str | None = None,
    ) -> ComplaintDraft:
        """Save or update complaint draft."""
        stmt = (
            select(ComplaintDraft)
            .where(ComplaintDraft.user_id == user_id)
            .order_by(ComplaintDraft.updated_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        draft = result.scalar_one_or_none()

        if draft is None:
            draft = ComplaintDraft(
                user_id=user_id,
                draft_data=json.dumps(draft_data, ensure_ascii=False),
                conversation_state=conversation_state,
            )
            session.add(draft)
        else:
            draft.draft_data = json.dumps(draft_data, ensure_ascii=False)
            draft.conversation_state = conversation_state
            draft.updated_at = utcnow()

        await session.commit()
        return draft

    @staticmethod
    async def get_draft(
        session: AsyncSession, user_id: int
    ) -> ComplaintDraft | None:
        """Get latest draft for user."""
        stmt = (
            select(ComplaintDraft)
            .where(ComplaintDraft.user_id == user_id)
            .order_by(ComplaintDraft.updated_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def delete_draft(session: AsyncSession, user_id: int, *, commit: bool = True) -> None:
        """Delete all drafts for user."""
        stmt = select(ComplaintDraft).where(ComplaintDraft.user_id == user_id)
        result = await session.execute(stmt)
        drafts = result.scalars().all()
        for draft in drafts:
            await session.delete(draft)
        if commit:
            await session.commit()
        else:
            await session.flush()


# ============================================================================
# Category & Organization Service
# ============================================================================

class CatalogService:
    """Manage categories, organizations, and MFY areas."""

    @staticmethod
    async def get_active_categories(
        session: AsyncSession, language: str = "uz"
    ) -> Sequence[Category]:
        """Get all active categories sorted by order."""
        stmt = (
            select(Category)
            .where(Category.is_active == True)  # noqa: E712
            .order_by(Category.sort_order, Category.id)
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_category_by_id(
        session: AsyncSession, category_id: int
    ) -> Category | None:
        """Get category by ID."""
        stmt = select(Category).where(Category.id == category_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_active_mfy_areas(
        session: AsyncSession,
    ) -> Sequence[MFYArea]:
        """Get all active MFY areas."""
        stmt = (
            select(MFYArea)
            .where(MFYArea.is_active == True)  # noqa: E712
            .order_by(MFYArea.sort_order, MFYArea.name_uz)
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_organizations_for_category(
        session: AsyncSession, category_id: int
    ) -> Sequence[Organization]:
        """Get organizations responsible for a category."""
        stmt = (
            select(Organization)
            .join(CategoryOrganization)
            .where(
                and_(
                    CategoryOrganization.category_id == category_id,
                    Organization.is_active == True,  # noqa: E712
                )
            )
        )
        result = await session.execute(stmt)
        return result.scalars().all()


# ============================================================================
# Assignment Service
# ============================================================================

class AssignmentService:
    """Manage complaint assignments to organizations and executors."""

    @staticmethod
    async def assign_complaint(
        session: AsyncSession,
        complaint_id: int,
        organization_id: int,
        assigned_by_id: int,
        assigned_to_id: int | None = None,
        is_primary: bool = True,
        notes: str | None = None,
        commit: bool = True,
    ) -> Assignment:
        """Assign a complaint to an organization/executor."""
        complaint = await session.get(Complaint, complaint_id)
        if complaint is None:
            raise ValueError(f"Complaint {complaint_id} not found")

        prior_primary: list[Assignment] = []
        if is_primary:
            prior_assignments = (await session.execute(
                select(Assignment).where(
                    Assignment.complaint_id == complaint_id,
                    Assignment.is_primary.is_(True),
                    Assignment.is_active.is_(True),
                ).with_for_update()
            )).scalars().all()
            prior_primary = prior_assignments
            for prior in prior_assignments:
                prior.is_active = False
                prior.unassigned_at = utcnow()

        assignment = Assignment(
            complaint_id=complaint_id,
            organization_id=organization_id,
            assigned_to_id=assigned_to_id,
            is_primary=is_primary,
            assigned_by_id=assigned_by_id,
            notes=notes,
        )
        session.add(assignment)

        # Audit
        audit = AuditEvent(
            action=AuditAction.COMPLAINT_ASSIGNED.value,
            entity_type="complaint",
            entity_id=str(complaint_id),
            actor_type="admin",
            actor_id=str(assigned_by_id),
            new_value=json.dumps({
                "organization_id": organization_id,
                "assigned_to_id": assigned_to_id,
                "is_primary": is_primary,
            }),
        )
        session.add(audit)

        # Queue a citizen message only when the responsible organization changes.
        # The notification worker delivers it after the assignment transaction commits.
        organization_changed = not any(
            prior.organization_id == organization_id for prior in prior_primary
        )
        if is_primary and organization_changed:
            user = await session.get(User, complaint.user_id)
            organization = await session.get(Organization, organization_id)
            if user and user.telegram_id and organization:
                language = "ru" if user.interface_language == "ru" else "uz"
                organization_name = organization.name_ru if language == "ru" else organization.name_uz
                if language == "ru":
                    message = (
                        f"📬 Ваше обращение направлено в ответственный орган.\n"
                        f"📋 Номер: {complaint.tracking_id}\n"
                        f"🏢 Ответственный орган: {organization_name}\n"
                        "Статус можно проверить в разделе «Мои обращения»."
                    )
                else:
                    message = (
                        f"📬 Murojaatingiz mas'ul idoraga yo'naltirildi.\n"
                        f"📋 Tracking raqami: {complaint.tracking_id}\n"
                        f"🏢 Mas'ul idora: {organization_name}\n"
                        "Holatini botdagi «Murojaatlarim» bo'limida kuzatishingiz mumkin."
                    )
                session.add(Notification(
                    complaint_id=complaint_id,
                    recipient_telegram_id=user.telegram_id,
                    notification_type="complaint_routed",
                    message_text=message,
                    language=language,
                ))

        await queue_assignment_notice(session, complaint, organization_id, assigned_to_id)
        if commit:
            await session.commit()
        else:
            await session.flush()
        return assignment


# ============================================================================
# Response Service
# ============================================================================

class ResponseService:
    """Manage agency responses to complaints."""

    @staticmethod
    async def add_response(
        session: AsyncSession,
        complaint_id: int,
        response_text: str,
        response_language: str,
        responded_by_id: int,
        actions_taken: str | None = None,
        is_final: bool = False,
    ) -> Response:
        """Add official response to a complaint."""
        response = Response(
            complaint_id=complaint_id,
            response_text=response_text,
            response_language=response_language,
            responded_by_id=responded_by_id,
            actions_taken=actions_taken,
            is_final=is_final,
        )
        session.add(response)

        # Audit
        audit = AuditEvent(
            action=AuditAction.RESPONSE_ADDED.value,
            entity_type="complaint",
            entity_id=str(complaint_id),
            actor_type="admin",
            actor_id=str(responded_by_id),
            new_value=json.dumps({
                "is_final": is_final,
                "language": response_language,
            }),
        )
        session.add(audit)

        recipient = (await session.execute(
            select(User.telegram_id, User.interface_language, Complaint.tracking_id)
            .join(Complaint, Complaint.user_id == User.id)
            .where(Complaint.id == complaint_id)
        )).one_or_none()
        if recipient and recipient.telegram_id:
            language = recipient.interface_language or "uz"
            header = (
                f"Ответ по обращению {recipient.tracking_id}:"
                if language == "ru"
                else f"{recipient.tracking_id} murojaatiga javob:"
            )
            # Telegram limits a message to 4096 UTF-16 code units. Queue every
            # part so the full official response is delivered and retryable.
            parts: list[str] = []
            current: list[str] = []
            units = 0
            for char in response_text:
                width = 2 if ord(char) > 0xFFFF else 1
                if current and units + width > 3000:
                    parts.append("".join(current))
                    current, units = [], 0
                current.append(char)
                units += width
            if current:
                parts.append("".join(current))
            for index, part in enumerate(parts, start=1):
                prefix = f"{header} ({index}/{len(parts)})" if len(parts) > 1 else header
                session.add(Notification(
                    complaint_id=complaint_id,
                    recipient_telegram_id=recipient.telegram_id,
                    notification_type="response",
                    message_text=f"{prefix}\n\n{part}",
                    language=language,
                ))

        await session.commit()
        return response


# ============================================================================
# Deadline Service
# ============================================================================

class DeadlineService:
    """Manage complaint deadlines and extensions."""

    @staticmethod
    async def get_approaching_deadlines(
        session: AsyncSession, days_before: int = 3
    ) -> Sequence[Deadline]:
        """Get complaints with deadlines approaching within N days."""
        from app.models import TERMINAL_STATUSES

        now = utcnow()
        threshold = now + timedelta(days=days_before)
        terminal_values = [s.value for s in TERMINAL_STATUSES]

        stmt = (
            select(Deadline)
            .join(Complaint)
            .where(
                and_(
                    Deadline.current_deadline <= threshold,
                    Deadline.current_deadline > now,
                    Deadline.warning_sent == False,  # noqa: E712
                    Complaint.archived_at.is_(None),
                    Complaint.deleted_at.is_(None),
                    ~Complaint.status.in_(terminal_values),
                )
            )
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_overdue_deadlines(
        session: AsyncSession,
    ) -> Sequence[Deadline]:
        """Get complaints past their deadline."""
        from app.models import TERMINAL_STATUSES

        now = utcnow()
        terminal_values = [s.value for s in TERMINAL_STATUSES]

        stmt = (
            select(Deadline)
            .join(Complaint)
            .where(
                and_(
                    Deadline.current_deadline < now,
                    Complaint.archived_at.is_(None),
                    Complaint.deleted_at.is_(None),
                    ~Complaint.status.in_(terminal_values),
                )
            )
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def extend_deadline(
        session: AsyncSession,
        deadline_id: int,
        new_deadline: datetime,
        reason: str,
        approved_by_id: int,
    ) -> DeadlineExtension:
        """Extend a deadline with a bounded reason and queue citizen notice."""
        reason = reason.strip() if isinstance(reason, str) else ""
        if not reason or len(reason) > 1000:
            raise ValueError("Extension reason must contain 1–1000 characters")

        stmt = select(Deadline).where(Deadline.id == deadline_id)
        result = await session.execute(stmt)
        deadline = result.scalar_one_or_none()

        if deadline is None:
            raise ValueError(f"Deadline {deadline_id} not found")

        previous_deadline = deadline.current_deadline
        if previous_deadline.tzinfo is None:
            previous_deadline = previous_deadline.replace(tzinfo=timezone.utc)
        if new_deadline.tzinfo is None:
            new_deadline = new_deadline.replace(tzinfo=timezone.utc)
        if new_deadline <= previous_deadline:
            raise ValueError("New deadline must be later than the current deadline")
        if new_deadline > previous_deadline + timedelta(days=365):
            raise ValueError("A single extension cannot exceed 365 days")

        extension = DeadlineExtension(
            deadline_id=deadline_id,
            previous_deadline=previous_deadline,
            new_deadline=new_deadline,
            reason=reason,
            approved_by_id=approved_by_id,
        )
        session.add(extension)
        await session.flush()

        deadline.current_deadline = new_deadline

        # Audit
        audit = AuditEvent(
            action=AuditAction.DEADLINE_EXTENDED.value,
            entity_type="complaint",
            entity_id=str(deadline.complaint_id),
            actor_type="admin",
            actor_id=str(approved_by_id),
            old_value=json.dumps({"deadline": extension.previous_deadline.isoformat()}),
            new_value=json.dumps({
                "deadline": new_deadline.isoformat(),
                "reason": reason,
            }),
        )
        session.add(audit)

        complaint = await session.get(Complaint, deadline.complaint_id)
        user = await session.get(User, complaint.user_id) if complaint else None
        if user and user.telegram_id:
            deadline_label = new_deadline.astimezone(
                ZoneInfo("Asia/Tashkent")
            ).strftime("%d.%m.%Y")
            language = user.interface_language or "uz"
            if language == "ru":
                message = (
                    f"Срок рассмотрения обращения {complaint.tracking_id} продлён. "
                    f"Новый срок: {deadline_label}."
                )
            else:
                message = (
                    f"{complaint.tracking_id} raqamli murojaatni ko‘rib chiqish "
                    f"muddati uzaytirildi. Yangi muddat: {deadline_label}."
                )
            session.add(Notification(
                complaint_id=complaint.id,
                deadline_extension_id=extension.id,
                recipient_telegram_id=user.telegram_id,
                notification_type="deadline_extended",
                message_text=message,
                language=language,
            ))

        await session.commit()
        return extension


# ============================================================================
# Statistics Service
# ============================================================================

class StatisticsService:
    """Aggregated statistics without PII."""

    @staticmethod
    async def get_complaint_counts(
        session: AsyncSession,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        category_id: int | None = None,
        organization_id: int | None = None,
        assigned_to_id: int | None = None,
    ) -> dict[str, int]:
        """Get complaint counts by status."""
        stmt = select(
            Complaint.status,
            func.count(Complaint.id),
        ).where(
            Complaint.archived_at.is_(None), Complaint.deleted_at.is_(None)
        ).group_by(Complaint.status)

        if date_from:
            stmt = stmt.where(Complaint.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Complaint.created_at <= date_to)
        if category_id:
            stmt = stmt.where(Complaint.category_id == category_id)
        if organization_id is not None:
            assignment_scope = select(Assignment.complaint_id).where(
                Assignment.organization_id == organization_id,
                Assignment.is_active.is_(True),
            )
            if assigned_to_id is not None:
                assignment_scope = assignment_scope.where(Assignment.assigned_to_id == assigned_to_id)
            stmt = stmt.where(Complaint.id.in_(assignment_scope))

        result = await session.execute(stmt)
        counts = {row[0]: row[1] for row in result.all()}

        return {
            "total": sum(counts.values()),
            "submitted": counts.get(ComplaintStatus.SUBMITTED.value, 0),
            "triage": counts.get(ComplaintStatus.TRIAGE.value, 0),
            "routed": counts.get(ComplaintStatus.ROUTED.value, 0),
            "in_progress": counts.get(ComplaintStatus.IN_PROGRESS.value, 0),
            "resolved": counts.get(ComplaintStatus.RESOLVED.value, 0),
            "reopened": counts.get(ComplaintStatus.REOPENED.value, 0),
            "rejected": counts.get(ComplaintStatus.REJECTED.value, 0),
            "overdue": 0,  # Calculated separately
        }

    @staticmethod
    async def get_resolution_time_stats(
        session: AsyncSession,
        organization_id: int | None = None,
        assigned_to_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Get resolution time statistics.
        Uses median/percentile, not just simple average.
        """
        stmt = select(
            Complaint.submitted_at,
            Complaint.resolved_at,
        ).where(
            and_(
                Complaint.resolved_at.isnot(None),
                Complaint.submitted_at.isnot(None),
                Complaint.archived_at.is_(None),
                Complaint.deleted_at.is_(None),
            )
        )
        if organization_id is not None:
            assignment_scope = select(Assignment.complaint_id).where(
                Assignment.organization_id == organization_id,
                Assignment.is_active.is_(True),
            )
            if assigned_to_id is not None:
                assignment_scope = assignment_scope.where(Assignment.assigned_to_id == assigned_to_id)
            stmt = stmt.where(Complaint.id.in_(assignment_scope))
        result = await session.execute(stmt)
        rows = result.all()

        if not rows:
            return {"count": 0, "median_hours": None, "p90_hours": None, "p95_hours": None}

        # Calculate resolution times in hours
        times = []
        for submitted, resolved in rows:
            if submitted and resolved:
                delta = (resolved - submitted).total_seconds() / 3600
                times.append(delta)

        times.sort()
        count = len(times)

        return {
            "count": count,
            "median_hours": round(statistics.median(times), 1) if times else None,
            "p90_hours": round(times[int(count * 0.9)], 1) if times else None,
            "p95_hours": round(times[math.ceil(count * 0.95) - 1], 1) if times else None,
            "min_hours": round(times[0], 1) if times else None,
            "max_hours": round(times[-1], 1) if times else None,
        }


# ============================================================================
# Admin Auth Service
# ============================================================================

class AdminAuthService:
    """Admin authentication and session management."""

    @staticmethod
    async def get_admin_by_username(
        session: AsyncSession, username: str
    ) -> AdminUser | None:
        """Get admin user by username."""
        stmt = select(AdminUser).where(AdminUser.username == username)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def check_lockout(
        session: AsyncSession, admin: AdminUser
    ) -> bool:
        """Check if admin account is locked out."""
        if admin.locked_until and admin.locked_until > utcnow():
            return True
        return False

    @staticmethod
    async def record_failed_login(
        session: AsyncSession, admin: AdminUser
    ) -> None:
        """Record failed login attempt and lock if needed."""
        settings = get_settings()
        admin.failed_login_attempts += 1

        if admin.failed_login_attempts >= settings.max_login_attempts:
            admin.locked_until = utcnow() + timedelta(
                seconds=settings.login_lockout_seconds
            )
            logger.warning(
                "Admin account locked",
                admin_id=admin.id,
                locked_until=str(admin.locked_until),
            )

        # Audit
        audit = AuditEvent(
            action=AuditAction.ADMIN_LOGIN_FAILED.value,
            entity_type="admin_user",
            entity_id=str(admin.id),
            actor_type="admin",
            actor_id=str(admin.id),
        )
        session.add(audit)
        await session.commit()

    @staticmethod
    async def record_successful_login(
        session: AsyncSession, admin: AdminUser
    ) -> None:
        """Reset failed attempts and record successful login."""
        admin.failed_login_attempts = 0
        admin.locked_until = None
        admin.last_login_at = utcnow()

        audit = AuditEvent(
            action=AuditAction.ADMIN_LOGIN.value,
            entity_type="admin_user",
            entity_id=str(admin.id),
            actor_type="admin",
            actor_id=str(admin.id),
        )
        session.add(audit)
        await session.commit()


# ============================================================================
# Audit Service
# ============================================================================

class AuditService:
    """Append-only audit trail. No update or delete operations."""

    @staticmethod
    async def log_event(
        session: AsyncSession,
        action: str,
        entity_type: str,
        actor_type: str,
        entity_id: str | None = None,
        actor_id: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        metadata: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditEvent:
        """Create an immutable audit event."""
        event = AuditEvent(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_type=actor_type,
            actor_id=actor_id,
            old_value=json.dumps(old_value) if old_value else None,
            new_value=json.dumps(new_value) if new_value else None,
            metadata_json=json.dumps(metadata) if metadata else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        session.add(event)
        await session.flush()
        return event
