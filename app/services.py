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
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_config import get_logger
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
    Organization,
    RateLimit,
    Response,
    StatusEvent,
    User,
    generate_tracking_id,
    utcnow,
)

logger = get_logger(__name__)


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
        """Check if user has accepted the current consent version."""
        stmt = (
            select(ConsentRecord)
            .where(
                and_(
                    ConsentRecord.user_id == user_id,
                    ConsentRecord.consent_version == current_version,
                    ConsentRecord.accepted == True,  # noqa: E712
                )
            )
            .order_by(ConsentRecord.accepted_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


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
    ) -> tuple[bool, int]:
        """
        Check if user is within rate limit.
        Returns (is_allowed, current_count).
        """
        settings = get_settings()
        max_per_day = settings.max_complaints_per_day
        now = utcnow()
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        stmt = select(RateLimit).where(
            and_(
                RateLimit.telegram_id == telegram_id,
                RateLimit.action == action,
                RateLimit.window_start == window_start,
            )
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            return True, 0

        return record.count < max_per_day, record.count

    @staticmethod
    async def increment_rate_limit(
        session: AsyncSession,
        telegram_id: int,
        action: str = "complaint",
    ) -> None:
        """Increment rate limit counter."""
        now = utcnow()
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        stmt = select(RateLimit).where(
            and_(
                RateLimit.telegram_id == telegram_id,
                RateLimit.action == action,
                RateLimit.window_start == window_start,
            )
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            record = RateLimit(
                telegram_id=telegram_id,
                action=action,
                window_start=window_start,
                count=1,
            )
            session.add(record)
        else:
            record.count += 1
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
        deadline_days = {
            ComplaintType.ARIZA.value: settings.default_ariza_deadline_days,
            ComplaintType.SHIKOYAT.value: settings.default_shikoyat_deadline_days,
            ComplaintType.TAKLIF.value: settings.default_taklif_deadline_days,
        }.get(complaint_type, 15)

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

        await session.commit()

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
        stmt = select(Complaint).where(Complaint.tracking_id == tracking_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_complaints(
        session: AsyncSession, user_id: int
    ) -> Sequence[Complaint]:
        """Get all complaints for a user, newest first."""
        stmt = (
            select(Complaint)
            .where(Complaint.user_id == user_id)
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
    async def delete_draft(session: AsyncSession, user_id: int) -> None:
        """Delete all drafts for user."""
        stmt = select(ComplaintDraft).where(ComplaintDraft.user_id == user_id)
        result = await session.execute(stmt)
        drafts = result.scalars().all()
        for draft in drafts:
            await session.delete(draft)
        await session.commit()


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
    ) -> Assignment:
        """Assign a complaint to an organization/executor."""
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

        await session.commit()
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
        """Extend a deadline with required reason and approver."""
        stmt = select(Deadline).where(Deadline.id == deadline_id)
        result = await session.execute(stmt)
        deadline = result.scalar_one_or_none()

        if deadline is None:
            raise ValueError(f"Deadline {deadline_id} not found")

        extension = DeadlineExtension(
            deadline_id=deadline_id,
            previous_deadline=deadline.current_deadline,
            new_deadline=new_deadline,
            reason=reason,
            approved_by_id=approved_by_id,
        )
        session.add(extension)

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
    ) -> dict[str, int]:
        """Get complaint counts by status."""
        stmt = select(
            Complaint.status,
            func.count(Complaint.id),
        ).group_by(Complaint.status)

        if date_from:
            stmt = stmt.where(Complaint.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Complaint.created_at <= date_to)
        if category_id:
            stmt = stmt.where(Complaint.category_id == category_id)

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
            )
        )
        result = await session.execute(stmt)
        rows = result.all()

        if not rows:
            return {"count": 0, "median_hours": None, "p90_hours": None}

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
            "median_hours": round(times[count // 2], 1) if times else None,
            "p90_hours": round(times[int(count * 0.9)], 1) if times else None,
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
