"""
Tests for core business services.
Tests complaint creation, status transitions, rate limiting,
duplicate detection, drafts, and user consent.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    CategoryOrganization,
    Complaint,
    ComplaintLanguage,
    ComplaintStatus,
    ComplaintType,
    Deadline,
    MFYArea,
    Notification,
    Organization,
    User,
    AdminUser,
    AdminRole,
    utcnow,
)
from app.services import (
    ComplaintService,
    DraftService,
    RateLimitService,
    UserService,
    CatalogService,
    AssignmentService,
    AuditService,
)


# ============================================================================
# Helpers
# ============================================================================

async def create_test_user(session: AsyncSession, telegram_id: int = 12345) -> User:
    """Create a test citizen user."""
    user = User(telegram_id=telegram_id, interface_language="uz")
    session.add(user)
    await session.flush()
    return user


async def create_test_category(session: AsyncSession) -> Category:
    """Create a test category."""
    cat = Category(
        code="test_education",
        name_uz="Ta'lim",
        name_uz_cyrillic="Таълим",
        name_ru="Образование",
        is_provisional=True,
    )
    session.add(cat)
    await session.flush()
    return cat


async def create_test_org(session: AsyncSession) -> Organization:
    """Create a test organization."""
    org = Organization(
        code="test_edu_dept",
        name_uz="Ta'lim bo'limi",
        name_uz_cyrillic="Таълим бўлими",
        name_ru="Отдел образования",
        is_provisional=True,
    )
    session.add(org)
    await session.flush()
    return org


async def create_test_admin(
    session: AsyncSession, role: str = AdminRole.SUPER_ADMIN.value, org_id: int | None = None
) -> AdminUser:
    """Create a test admin user."""
    import bcrypt
    admin = AdminUser(
        username=f"testadmin_{role}",
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
        full_name="Test Admin",
        role=role,
        organization_id=org_id,
    )
    session.add(admin)
    await session.flush()
    return admin


async def create_test_mfy(session: AsyncSession) -> MFYArea:
    """Create a test MFY area."""
    mfy = MFYArea(
        name_uz="Tinchlik MFY",
        name_uz_cyrillic="Тинчлик МФЙ",
        name_ru="МСГ Тинчлик",
        is_provisional=True,
    )
    session.add(mfy)
    await session.flush()
    return mfy


async def create_test_complaint(
    session: AsyncSession,
    user: User,
    category: Category,
    mfy: MFYArea | None = None,
) -> Complaint:
    """Create a test complaint using the service."""
    return await ComplaintService.create_complaint(
        session=session,
        user_id=user.id,
        complaint_type=ComplaintType.ARIZA.value,
        complaint_language=ComplaintLanguage.UZ_LATIN.value,
        category_id=category.id,
        full_name="Test Foydalanuvchi Testov",
        phone_number="+998901234567",
        title="Test murojaat",
        complaint_text="Bu test murojaat matni.",
        mfy_area_id=mfy.id if mfy else None,
        address_detail="Test ko'chasi, 1-uy",
    )


# ============================================================================
# User Service Tests
# ============================================================================

class TestUserService:
    """Test user creation and consent management."""

    @pytest.mark.asyncio
    async def test_create_new_user(self, db_session: AsyncSession):
        user = await UserService.get_or_create_user(db_session, telegram_id=11111)
        assert user.id is not None
        assert user.telegram_id == 11111
        assert user.interface_language == "uz"

    @pytest.mark.asyncio
    async def test_get_existing_user(self, db_session: AsyncSession):
        user1 = await UserService.get_or_create_user(db_session, telegram_id=22222)
        user2 = await UserService.get_or_create_user(db_session, telegram_id=22222)
        assert user1.id == user2.id

    @pytest.mark.asyncio
    async def test_consent_recording(self, db_session: AsyncSession):
        user = await create_test_user(db_session, telegram_id=33333)
        await db_session.commit()

        # No consent yet
        has_consent = await UserService.has_valid_consent(db_session, user.id, "1.0-draft")
        assert has_consent is False

        # Record consent
        record = await UserService.record_consent(
            db_session, user.id, "1.0-draft", "consent text here", True
        )
        await db_session.commit()
        assert record.accepted is True

        # Now has consent
        has_consent = await UserService.has_valid_consent(db_session, user.id, "1.0-draft")
        assert has_consent is True

    @pytest.mark.asyncio
    async def test_consent_version_mismatch(self, db_session: AsyncSession):
        user = await create_test_user(db_session, telegram_id=44444)
        await db_session.commit()

        await UserService.record_consent(
            db_session, user.id, "1.0-draft", "old text", True
        )
        await db_session.commit()

        # Different version should not have consent
        has_consent = await UserService.has_valid_consent(db_session, user.id, "2.0")
        assert has_consent is False

    @pytest.mark.asyncio
    async def test_latest_consent_decision_can_revoke_previous_acceptance(self, db_session: AsyncSession):
        user = await create_test_user(db_session, telegram_id=45555)
        await UserService.record_consent(db_session, user.id, "1.0-draft", "terms", True)
        await db_session.commit()
        assert await UserService.has_valid_consent(db_session, user.id, "1.0-draft") is True

        await UserService.record_consent(db_session, user.id, "1.0-draft", "terms", False)
        await db_session.commit()
        assert await UserService.has_valid_consent(db_session, user.id, "1.0-draft") is False


# ============================================================================
# Complaint Service Tests
# ============================================================================

class TestComplaintService:
    """Test complaint creation and lifecycle."""

    @pytest.mark.asyncio
    async def test_create_complaint(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        complaint = await create_test_complaint(db_session, user, category)

        assert complaint.id is not None
        assert complaint.tracking_id.startswith("FTMT-")
        assert complaint.status == ComplaintStatus.SUBMITTED.value
        assert complaint.submitted_at is not None
        assert complaint.phone_verified is False  # OTP not implemented

    @pytest.mark.asyncio
    async def test_assignment_queues_localized_citizen_notice_with_organization(self, db_session: AsyncSession):
        user = await create_test_user(db_session, telegram_id=12347)
        category = await create_test_category(db_session)
        organization = await create_test_org(db_session)
        admin = await create_test_admin(db_session)
        complaint = await create_test_complaint(db_session, user, category)

        await AssignmentService.assign_complaint(
            db_session, complaint.id, organization.id, admin.id, commit=False
        )
        await ComplaintService.transition_status(
            db_session, complaint.id, ComplaintStatus.TRIAGE, "admin", str(admin.id),
            notify_citizen=False, commit=False,
        )
        await ComplaintService.transition_status(
            db_session, complaint.id, ComplaintStatus.ROUTED, "admin", str(admin.id),
            notify_citizen=False, commit=False,
        )
        await db_session.commit()

        notice = (await db_session.execute(
            select(Notification).where(
                Notification.complaint_id == complaint.id,
                Notification.notification_type == "complaint_routed",
            )
        )).scalar_one()
        assert organization.name_uz in notice.message_text
        assert complaint.tracking_id in notice.message_text
        assert notice.recipient_telegram_id == user.telegram_id
        assert complaint.status == ComplaintStatus.ROUTED.value

        # Reassigning to the same organization should not send a duplicate notice.
        await AssignmentService.assign_complaint(
            db_session, complaint.id, organization.id, admin.id, commit=False
        )
        await db_session.commit()
        notices = (await db_session.execute(
            select(Notification).where(
                Notification.complaint_id == complaint.id,
                Notification.notification_type == "complaint_routed",
            )
        )).scalars().all()
        assert len(notices) == 1

    @pytest.mark.asyncio
    async def test_explicit_deadline_days_are_applied_and_bounded(self, db_session: AsyncSession):
        user = await create_test_user(db_session, telegram_id=12346)
        category = await create_test_category(db_session)
        complaint = await ComplaintService.create_complaint(
            session=db_session, user_id=user.id,
            complaint_type=ComplaintType.ARIZA.value,
            complaint_language=ComplaintLanguage.UZ_LATIN.value,
            category_id=category.id, full_name="Synthetic Citizen",
            phone_number="+998901234568", title="Synthetic SLA check",
            complaint_text="Synthetic test only", deadline_days=7,
        )
        deadline = (await db_session.execute(
            select(Deadline).where(Deadline.complaint_id == complaint.id)
        )).scalar_one()
        deadline_utc = deadline.current_deadline.replace(tzinfo=timezone.utc)
        submitted_utc = complaint.submitted_at.astimezone(timezone.utc)
        assert deadline_utc - submitted_utc == timedelta(days=7)

        with pytest.raises(ValueError, match="deadline_days"):
            await ComplaintService.create_complaint(
                session=db_session, user_id=user.id,
                complaint_type=ComplaintType.ARIZA.value,
                complaint_language=ComplaintLanguage.UZ_LATIN.value,
                category_id=category.id, full_name="Synthetic Citizen",
                phone_number="+998901234568", title="Invalid SLA check",
                complaint_text="Synthetic test only", deadline_days=0,
            )
        await db_session.rollback()


class TestRollingRateLimit:
    @pytest.mark.asyncio
    async def test_slot_reservation_counts_rolling_24_hours(self, db_session: AsyncSession):
        user_id = 67890
        for expected_count in range(1, 4):
            async with RateLimitService.reserve_complaint_slot(db_session, user_id, max_per_day=3) as result:
                assert result == (True, expected_count)
                await db_session.commit()

        async with RateLimitService.reserve_complaint_slot(db_session, user_id, max_per_day=3) as result:
            assert result == (False, 3)

    @pytest.mark.asyncio
    async def test_old_events_do_not_count_towards_rolling_window(self, db_session: AsyncSession):
        from datetime import timedelta
        from app.models import RateLimit

        db_session.add_all([
            RateLimit(telegram_id=67891, action="complaint", window_start=utcnow() - timedelta(hours=23), count=1),
            RateLimit(telegram_id=67891, action="complaint", window_start=utcnow() - timedelta(hours=25), count=8),
        ])
        await db_session.commit()
        assert await RateLimitService.check_rate_limit(db_session, 67891) == (True, 1)

    @pytest.mark.asyncio
    async def test_unique_tracking_ids(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        complaints = []
        for _ in range(5):
            c = await create_test_complaint(db_session, user, category)
            complaints.append(c)

        tracking_ids = {c.tracking_id for c in complaints}
        assert len(tracking_ids) == 5  # All unique

    @pytest.mark.asyncio
    async def test_valid_status_transition(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        complaint = await create_test_complaint(db_session, user, category)

        # SUBMITTED → TRIAGE
        updated = await ComplaintService.transition_status(
            db_session, complaint.id, ComplaintStatus.TRIAGE, "admin", "1"
        )
        assert updated.status == ComplaintStatus.TRIAGE.value

    @pytest.mark.asyncio
    async def test_invalid_status_transition(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        complaint = await create_test_complaint(db_session, user, category)

        # SUBMITTED → RESOLVED should fail (skips steps)
        with pytest.raises(ValueError, match="Invalid transition"):
            await ComplaintService.transition_status(
                db_session, complaint.id, ComplaintStatus.RESOLVED, "admin", "1"
            )

    @pytest.mark.asyncio
    async def test_citizen_confirmation_resolved(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        complaint = await create_test_complaint(db_session, user, category)

        # Walk through happy path to CITIZEN_CONFIRMATION_PENDING
        for status in [
            ComplaintStatus.TRIAGE,
            ComplaintStatus.ROUTED,
            ComplaintStatus.IN_PROGRESS,
            ComplaintStatus.RESPONSE_PROVIDED,
            ComplaintStatus.IMPLEMENTATION_REPORTED,
            ComplaintStatus.CITIZEN_CONFIRMATION_PENDING,
        ]:
            await ComplaintService.transition_status(
                db_session, complaint.id, status, "admin", "1"
            )

        # Citizen confirms resolved
        resolved = await ComplaintService.confirm_resolved(
            db_session, complaint.id, user.id
        )
        assert resolved.status == ComplaintStatus.RESOLVED.value
        assert resolved.citizen_confirmed_at is not None

    @pytest.mark.asyncio
    async def test_citizen_reopens_complaint(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        complaint = await create_test_complaint(db_session, user, category)

        # Walk to CITIZEN_CONFIRMATION_PENDING
        for status in [
            ComplaintStatus.TRIAGE,
            ComplaintStatus.ROUTED,
            ComplaintStatus.IN_PROGRESS,
            ComplaintStatus.RESPONSE_PROVIDED,
            ComplaintStatus.IMPLEMENTATION_REPORTED,
            ComplaintStatus.CITIZEN_CONFIRMATION_PENDING,
        ]:
            await ComplaintService.transition_status(
                db_session, complaint.id, status, "admin", "1"
            )

        # Citizen says NOT resolved
        reopened = await ComplaintService.reopen_complaint(
            db_session, complaint.id, user.id, "Muammo hali hal bo'lmadi"
        )
        assert reopened.status == ComplaintStatus.REOPENED.value

    @pytest.mark.asyncio
    async def test_get_user_complaints(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        # Create 3 complaints
        for _ in range(3):
            await create_test_complaint(db_session, user, category)

        complaints = await ComplaintService.get_user_complaints(db_session, user.id)
        assert len(complaints) == 3

    @pytest.mark.asyncio
    async def test_duplicate_hint_detection(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        # Create an open complaint
        await create_test_complaint(db_session, user, category)

        # Check for duplicate hint
        dup = await ComplaintService.check_duplicate_hint(
            db_session, user.id, category.id
        )
        assert dup is not None
        assert dup.tracking_id.startswith("FTMT-")

    @pytest.mark.asyncio
    async def test_no_duplicate_for_resolved(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        category = await create_test_category(db_session)
        await db_session.commit()

        complaint = await create_test_complaint(db_session, user, category)

        # Walk to RESOLVED
        for status in [
            ComplaintStatus.TRIAGE,
            ComplaintStatus.ROUTED,
            ComplaintStatus.IN_PROGRESS,
            ComplaintStatus.RESPONSE_PROVIDED,
            ComplaintStatus.IMPLEMENTATION_REPORTED,
            ComplaintStatus.CITIZEN_CONFIRMATION_PENDING,
            ComplaintStatus.RESOLVED,
        ]:
            await ComplaintService.transition_status(
                db_session, complaint.id, status, "admin", "1"
            )

        # No duplicate hint for resolved complaint
        dup = await ComplaintService.check_duplicate_hint(
            db_session, user.id, category.id
        )
        assert dup is None


# ============================================================================
# Rate Limit Tests
# ============================================================================

class TestRateLimitService:
    """Test rate limiting."""

    @pytest.mark.asyncio
    async def test_first_action_allowed(self, db_session: AsyncSession):
        allowed, count = await RateLimitService.check_rate_limit(
            db_session, telegram_id=99999
        )
        assert allowed is True
        assert count == 0

    @pytest.mark.asyncio
    async def test_increment_and_check(self, db_session: AsyncSession):
        tid = 88888
        await RateLimitService.increment_rate_limit(db_session, tid)
        await db_session.commit()

        allowed, count = await RateLimitService.check_rate_limit(db_session, tid)
        assert allowed is True
        assert count == 1

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self, db_session: AsyncSession):
        tid = 77777
        # Exercise a three-complaint policy independently of the deployment default.
        for _ in range(3):
            await RateLimitService.increment_rate_limit(db_session, tid)
            await db_session.commit()

        allowed, count = await RateLimitService.check_rate_limit(db_session, tid, max_per_day=3)
        assert allowed is False
        assert count == 3


# ============================================================================
# Draft Service Tests
# ============================================================================

class TestDraftService:
    """Test draft save/restore/delete."""

    @pytest.mark.asyncio
    async def test_save_and_get_draft(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        await db_session.commit()

        draft_data = {"step": "name", "full_name": "Test User"}
        await DraftService.save_draft(db_session, user.id, draft_data, "NAME")

        draft = await DraftService.get_draft(db_session, user.id)
        assert draft is not None
        assert "Test User" in draft.draft_data
        assert draft.conversation_state == "NAME"

    @pytest.mark.asyncio
    async def test_update_draft(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        await db_session.commit()

        await DraftService.save_draft(db_session, user.id, {"step": 1}, "STEP1")
        await DraftService.save_draft(db_session, user.id, {"step": 2}, "STEP2")

        draft = await DraftService.get_draft(db_session, user.id)
        assert draft is not None
        assert '"step": 2' in draft.draft_data
        assert draft.conversation_state == "STEP2"

    @pytest.mark.asyncio
    async def test_delete_draft(self, db_session: AsyncSession):
        user = await create_test_user(db_session)
        await db_session.commit()

        await DraftService.save_draft(db_session, user.id, {"test": True})
        await DraftService.delete_draft(db_session, user.id)

        draft = await DraftService.get_draft(db_session, user.id)
        assert draft is None


# ============================================================================
# i18n Tests
# ============================================================================

class TestI18n:
    """Test internationalization."""

    def test_get_uzbek_text(self):
        from app.i18n import t
        text = t("welcome", "uz")
        assert "Farg'ona" in text

    def test_get_russian_text(self):
        from app.i18n import t
        text = t("welcome", "ru")
        assert "Ферганского" in text

    def test_missing_key_returns_placeholder(self):
        from app.i18n import t
        text = t("nonexistent_key", "uz")
        assert "[Missing:" in text

    def test_format_parameters(self):
        from app.i18n import t
        text = t("rate_limit_exceeded", "uz", max=3)
        assert "3" in text

    def test_status_labels_exist(self):
        from app.i18n import get_status_label
        from app.models import ComplaintStatus
        for status in ComplaintStatus:
            label_uz = get_status_label(status.value, "uz")
            label_ru = get_status_label(status.value, "ru")
            assert "[Missing:" not in label_uz, f"Missing UZ label for {status.value}"
            assert "[Missing:" not in label_ru, f"Missing RU label for {status.value}"

    def test_type_labels_exist(self):
        from app.i18n import get_type_label
        from app.models import ComplaintType
        for ctype in ComplaintType:
            label_uz = get_type_label(ctype.value, "uz")
            label_ru = get_type_label(ctype.value, "ru")
            assert "[Missing:" not in label_uz
            assert "[Missing:" not in label_ru

    def test_consent_text_has_placeholder_warning(self):
        from app.i18n import t
        for lang in ["uz", "ru"]:
            text = t("consent_text", lang)
            # Must contain warning about not being approved by lawyer
            assert "yurist" in text.lower() or "юрист" in text.lower()

    def test_complaint_receipt_distinguishes_received_from_routed(self):
        from app.i18n import t
        uz = t("complaint_submitted", "uz", tracking_id="TEST-001", date="2026-01-01", type="ariza", category="Test")
        ru = t("complaint_submitted", "ru", tracking_id="TEST-001", date="2026-01-01", type="ariza", category="Test")
        assert "hali biriktirilmagan" in uz
        assert "alohida yuboramiz" in uz
        assert "пока не назначен" in ru
        assert "отправим вам название органа" in ru
        assert "15 kun" not in uz
        assert "15 дней" not in ru


# ============================================================================
# Logging Redaction Tests
# ============================================================================

class TestLoggingRedaction:
    """Test PII redaction in logs."""

    def test_phone_redaction(self):
        from app.logging_config import PIIRedactingFormatter
        import logging
        formatter = PIIRedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="User phone: +998901234567", args=None, exc_info=None,
        )
        formatted = formatter.format(record)
        assert "+998901234567" not in formatted
        assert "[PHONE_REDACTED]" in formatted

    def test_pii_field_sanitization(self):
        from app.logging_config import StructuredLogger
        logger = StructuredLogger("test")
        sanitized = logger._sanitize_kwargs({
            "full_name": "John Doe",
            "phone": "+998901234567",
            "complaint_id": 123,
        })
        assert sanitized["full_name"] == "[PII_REDACTED]"
        assert sanitized["phone"] == "[PII_REDACTED]"
        assert sanitized["complaint_id"] == 123  # Non-PII preserved


@pytest.mark.asyncio
async def test_staff_notices_are_queued_only_for_authorized_recipients(db_session):
    from app.services import ResponseService

    org = await create_test_org(db_session)
    category = await create_test_category(db_session)
    supervisor = AdminUser(
        username="notice-supervisor", password_hash="test", full_name="Supervisor",
        role=AdminRole.SUPER_ADMIN.value, telegram_id=4_000_000_001,
    )
    head = AdminUser(
        username="notice-head", password_hash="test", full_name="Head",
        role=AdminRole.AGENCY_HEAD.value, organization_id=org.id,
        telegram_id=4_000_000_002,
    )
    executor = AdminUser(
        username="notice-executor", password_hash="test", full_name="Executor",
        role=AdminRole.EXECUTOR.value, organization_id=org.id,
        telegram_id=4_000_000_003,
    )
    unrelated = AdminUser(
        username="notice-unrelated", password_hash="test", full_name="Unrelated",
        role=AdminRole.EXECUTOR.value, organization_id=org.id,
        telegram_id=4_000_000_004,
    )
    db_session.add_all([supervisor, head, executor, unrelated])
    await db_session.flush()
    citizen = await UserService.get_or_create_user(db_session, 4_000_000_005)
    complaint = await ComplaintService.create_complaint(
        db_session, citizen.id, ComplaintType.ARIZA.value,
        ComplaintLanguage.UZ_LATIN.value, category.id,
        "Synthetic Citizen", "+998900000000", "Synthetic title", "Synthetic issue",
    )
    await AssignmentService.assign_complaint(
        db_session, complaint.id, org.id, supervisor.id, executor.id,
    )
    notices = (await db_session.execute(select(Notification))).scalars().all()
    by_kind = {(item.notification_type, item.recipient_telegram_id) for item in notices}
    assert ("citizen_registered", supervisor.telegram_id) in by_kind
    assert ("complaint_received", supervisor.telegram_id) in by_kind
    assert ("staff_assignment", head.telegram_id) in by_kind
    assert ("staff_assignment", executor.telegram_id) in by_kind
    assert unrelated.telegram_id not in {item.recipient_telegram_id for item in notices}
    assert all("Synthetic Citizen" not in item.message_text for item in notices)

    long_response = "🙂" * 3000
    await ResponseService.add_response(
        db_session, complaint.id, long_response, "uz", supervisor.id,
    )
    response_notices = (await db_session.execute(select(Notification).where(
        Notification.notification_type == "response",
    ).order_by(Notification.id))).scalars().all()
    assert len(response_notices) > 1
    assert "".join(item.message_text.split("\n\n", 1)[1] for item in response_notices) == long_response
    assert all(len(item.message_text.encode("utf-16-le")) // 2 < 4096 for item in response_notices)
