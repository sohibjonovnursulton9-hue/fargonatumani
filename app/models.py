"""
SQLAlchemy models for the FTMT complaints management system.

Key design decisions:
- All timestamps stored in UTC, displayed in Asia/Tashkent
- Immutable audit trail (append-only, no updates/deletes)
- Status changes tracked as events (event sourcing pattern for complaint lifecycle)
- Unique tracking IDs for citizen-facing reference
- Soft deletes where needed (is_active flags)
- PII fields clearly identified for log redaction
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================================================
# Enums
# ============================================================================

class InterfaceLanguage(str, enum.Enum):
    """Bot interface language (menus, buttons, instructions)."""
    UZ = "uz"  # O'zbekcha
    RU = "ru"  # Русский


class ComplaintLanguage(str, enum.Enum):
    """Language of the complaint text itself."""
    UZ_LATIN = "uz_latin"     # O'zbek tili — lotin yozuvi
    UZ_CYRILLIC = "uz_cyrillic"  # Ўзбек тили — кирилл ёзуви
    RU = "ru"                  # Русский язык


class ComplaintType(str, enum.Enum):
    """Type of citizen complaint."""
    ARIZA = "ariza"        # Application/request
    SHIKOYAT = "shikoyat"  # Complaint
    TAKLIF = "taklif"      # Suggestion/proposal


class ComplaintStatus(str, enum.Enum):
    """
    Complaint lifecycle states (finite state machine).
    See docs/WORKFLOWS.md for transition rules.
    """
    DRAFT = "draft"
    SUBMITTED = "submitted"
    TRIAGE = "triage"
    ROUTED = "routed"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_CITIZEN = "waiting_for_citizen"
    RESPONSE_PROVIDED = "response_provided"
    IMPLEMENTATION_REPORTED = "implementation_reported"
    CITIZEN_CONFIRMATION_PENDING = "citizen_confirmation_pending"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    REFERRED_OUTSIDE = "referred_outside"
    OUT_OF_SCOPE = "out_of_scope"


# Valid status transitions
VALID_TRANSITIONS: dict[ComplaintStatus, set[ComplaintStatus]] = {
    ComplaintStatus.DRAFT: {ComplaintStatus.SUBMITTED, ComplaintStatus.WITHDRAWN},
    ComplaintStatus.SUBMITTED: {ComplaintStatus.TRIAGE},
    ComplaintStatus.TRIAGE: {
        ComplaintStatus.ROUTED,
        ComplaintStatus.REJECTED,
        ComplaintStatus.REFERRED_OUTSIDE,
        ComplaintStatus.OUT_OF_SCOPE,
    },
    ComplaintStatus.ROUTED: {
        ComplaintStatus.IN_PROGRESS,
        ComplaintStatus.REJECTED,
        ComplaintStatus.REFERRED_OUTSIDE,
    },
    ComplaintStatus.IN_PROGRESS: {
        ComplaintStatus.WAITING_FOR_CITIZEN,
        ComplaintStatus.RESPONSE_PROVIDED,
        ComplaintStatus.REFERRED_OUTSIDE,
    },
    ComplaintStatus.WAITING_FOR_CITIZEN: {
        ComplaintStatus.IN_PROGRESS,
        ComplaintStatus.WITHDRAWN,
    },
    ComplaintStatus.RESPONSE_PROVIDED: {
        ComplaintStatus.IMPLEMENTATION_REPORTED,
    },
    ComplaintStatus.IMPLEMENTATION_REPORTED: {
        ComplaintStatus.CITIZEN_CONFIRMATION_PENDING,
    },
    ComplaintStatus.CITIZEN_CONFIRMATION_PENDING: {
        ComplaintStatus.RESOLVED,
        ComplaintStatus.REOPENED,
    },
    ComplaintStatus.RESOLVED: set(),  # Terminal state
    ComplaintStatus.REOPENED: {
        ComplaintStatus.TRIAGE,
        ComplaintStatus.IN_PROGRESS,
    },
    ComplaintStatus.REJECTED: set(),  # Terminal, but can be appealed via new complaint
    ComplaintStatus.WITHDRAWN: set(),  # Terminal
    ComplaintStatus.REFERRED_OUTSIDE: set(),  # Terminal for this district
    ComplaintStatus.OUT_OF_SCOPE: set(),  # Terminal
}

TERMINAL_STATUSES = {
    status for status, transitions in VALID_TRANSITIONS.items() if not transitions
}


class AdminRole(str, enum.Enum):
    """Role-based access control roles."""
    SUPER_ADMIN = "super_admin"
    DISTRICT_SUPERVISOR = "district_supervisor"
    AGENCY_HEAD = "agency_head"
    EXECUTOR = "executor"
    AUDITOR = "auditor"


class AuditAction(str, enum.Enum):
    """Types of auditable actions."""
    # Complaint actions
    COMPLAINT_CREATED = "complaint_created"
    COMPLAINT_SUBMITTED = "complaint_submitted"
    STATUS_CHANGED = "status_changed"
    COMPLAINT_ASSIGNED = "complaint_assigned"
    COMPLAINT_REASSIGNED = "complaint_reassigned"
    RESPONSE_ADDED = "response_added"
    ATTACHMENT_ADDED = "attachment_added"
    CITIZEN_CONFIRMED = "citizen_confirmed"
    CITIZEN_REJECTED = "citizen_rejected"
    DEADLINE_EXTENDED = "deadline_extended"
    # Admin actions
    ADMIN_LOGIN = "admin_login"
    ADMIN_LOGIN_FAILED = "admin_login_failed"
    ADMIN_LOGOUT = "admin_logout"
    ADMIN_CREATED = "admin_created"
    ADMIN_UPDATED = "admin_updated"
    ADMIN_DEACTIVATED = "admin_deactivated"
    ROLE_CHANGED = "role_changed"
    CONFIG_CHANGED = "config_changed"
    DATA_EXPORTED = "data_exported"
    BULK_ACTION = "bulk_action"


# ============================================================================
# Helper
# ============================================================================

def generate_tracking_id() -> str:
    """
    Generate unique citizen-facing tracking ID.
    Format: FTMT-YYYYMMDD-XXXX (e.g., FTMT-20260926-A3F7)
    """
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d")
    random_part = uuid.uuid4().hex[:4].upper()
    return f"FTMT-{date_part}-{random_part}"


def utcnow() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


# ============================================================================
# Models
# ============================================================================

class User(Base):
    """
    Citizen user identified by Telegram user ID.
    PII fields: full_name, phone_number — MUST be redacted in logs.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    interface_language: Mapped[str] = mapped_column(
        String(5), default=InterfaceLanguage.UZ.value, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    consent_records: Mapped[list["ConsentRecord"]] = relationship(back_populates="user")
    complaints: Mapped[list["Complaint"]] = relationship(back_populates="user")
    drafts: Mapped[list["ComplaintDraft"]] = relationship(back_populates="user")


class ConsentRecord(Base):
    """
    Records of user consent for privacy/terms.
    Immutable — never updated or deleted, only new records added.
    """
    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(50), nullable=False)
    consent_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ip_info: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Telegram doesn't provide IP

    user: Mapped["User"] = relationship(back_populates="consent_records")


class Category(Base):
    """
    Complaint category (e.g., Education, Healthcare).
    Admin-managed catalog. Initial data is PROVISIONAL/DEMO.
    """
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name_uz: Mapped[str] = mapped_column(String(255), nullable=False)
    name_uz_cyrillic: Mapped[str] = mapped_column(String(255), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    description_uz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_provisional: Mapped[bool] = mapped_column(
        Boolean, default=True,
        comment="True = DEMO data, not officially confirmed"
    )
    # Emergency category flag
    is_emergency: Mapped[bool] = mapped_column(
        Boolean, default=False,
        comment="Categories requiring urgent/emergency handling"
    )
    emergency_guidance_uz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    emergency_guidance_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # SLA/deadline override for this category (in days). NULL = use system default.
    sla_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    organizations: Mapped[list["Organization"]] = relationship(
        secondary="category_organizations", back_populates="categories"
    )
    complaints: Mapped[list["Complaint"]] = relationship(back_populates="category")


class Organization(Base):
    """
    Government organization/agency responsible for handling complaints.
    PROVISIONAL — must be confirmed with hokimlik.
    """
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name_uz: Mapped[str] = mapped_column(String(255), nullable=False)
    name_uz_cyrillic: Mapped[str] = mapped_column(String(255), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    contact_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    categories: Mapped[list["Category"]] = relationship(
        secondary="category_organizations", back_populates="organizations"
    )
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="organization")
    admin_users: Mapped[list["AdminUser"]] = relationship(back_populates="organization")


class CategoryOrganization(Base):
    """Many-to-many: which organizations handle which categories."""
    __tablename__ = "category_organizations"

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"), primary_key=True
    )
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class MFYArea(Base):
    """
    Mahalla Fuqarolar Yig'ini (MFY) / settlement area.
    DEMO DATA — official list must be obtained from hokimlik.
    """
    __tablename__ = "mfy_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name_uz: Mapped[str] = mapped_column(String(255), nullable=False)
    name_uz_cyrillic: Mapped[str] = mapped_column(String(255), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    district: Mapped[str] = mapped_column(
        String(255), default="Farg'ona tumani", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_provisional: Mapped[bool] = mapped_column(
        Boolean, default=True,
        comment="True = DEMO data, not from official hokimlik list"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    complaints: Mapped[list["Complaint"]] = relationship(back_populates="mfy_area")


class ComplaintDraft(Base):
    """
    Saved draft for complaint in progress.
    Allows citizen to resume after timeout/restart.
    Auto-cleaned after 30 days of inactivity.
    """
    __tablename__ = "complaint_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    draft_data: Mapped[str] = mapped_column(Text, nullable=False, comment="JSON serialized draft")
    conversation_state: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="Bot conversation handler state"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="drafts")


class Complaint(Base):
    """
    Core complaint record.
    PII fields: full_name, phone_number, address_detail, complaint_text — MUST be redacted in logs.
    """
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracking_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True,
        comment="Citizen-facing unique tracking ID: FTMT-YYYYMMDD-XXXX"
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # Complaint metadata
    complaint_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="ariza/shikoyat/taklif"
    )
    complaint_language: Mapped[str] = mapped_column(
        String(15), nullable=False, comment="uz_latin/uz_cyrillic/ru"
    )
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False, index=True)

    # PII — citizen personal information
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="PII: F.I.Sh.")
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False, comment="PII: +998XXXXXXXXX")
    phone_verified: Mapped[bool] = mapped_column(
        Boolean, default=False,
        comment="False until OTP verification is implemented"
    )
    mfy_area_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mfy_areas.id"), nullable=True, index=True
    )
    address_detail: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="PII: detailed address"
    )
    birth_date: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
        comment="DISABLED by default. See LEGAL-OPEN-QUESTIONS.md"
    )

    # Complaint content
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    complaint_text: Mapped[str] = mapped_column(Text, nullable=False, comment="PII: complaint details")

    # Status
    status: Mapped[str] = mapped_column(
        String(40), default=ComplaintStatus.SUBMITTED.value, nullable=False, index=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    citizen_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="complaints")
    category: Mapped["Category"] = relationship(back_populates="complaints")
    mfy_area: Mapped[Optional["MFYArea"]] = relationship(back_populates="complaints")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="complaint")
    status_events: Mapped[list["StatusEvent"]] = relationship(
        back_populates="complaint", order_by="StatusEvent.created_at"
    )
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="complaint")
    responses: Mapped[list["Response"]] = relationship(back_populates="complaint")
    deadline: Mapped[Optional["Deadline"]] = relationship(back_populates="complaint", uselist=False)
    notifications: Mapped[list["Notification"]] = relationship(back_populates="complaint")

    __table_args__ = (
        Index("ix_complaints_status_created", "status", "created_at"),
        Index("ix_complaints_category_status", "category_id", "status"),
    )


class Attachment(Base):
    """
    File attachments for complaints and responses.
    Stores Telegram file_id by default; local download is optional.
    """
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"), nullable=False, index=True)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="photo/video/document")
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_extension: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    local_path: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="Local download path, if downloaded"
    )
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    uploaded_by: Mapped[str] = mapped_column(
        String(20), default="citizen", comment="citizen or admin_user_id"
    )
    is_response_attachment: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    complaint: Mapped["Complaint"] = relationship(back_populates="attachments")


class StatusEvent(Base):
    """
    Immutable audit trail of complaint status changes.
    APPEND ONLY — no updates or deletes allowed.
    """
    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"), nullable=False, index=True)
    from_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="system/citizen/admin"
    )
    actor_id: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="admin_user.id or user.telegram_id"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    complaint: Mapped["Complaint"] = relationship(back_populates="status_events")

    __table_args__ = (
        Index("ix_status_events_complaint_created", "complaint_id", "created_at"),
    )


class Assignment(Base):
    """
    Assignment of complaint to organization and/or specific executor.
    Supports primary + secondary assignees.
    """
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"), nullable=False, index=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    assigned_to_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True, index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    unassigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    complaint: Mapped["Complaint"] = relationship(back_populates="assignments")
    organization: Mapped["Organization"] = relationship(back_populates="assignments")
    assigned_to: Mapped[Optional["AdminUser"]] = relationship(
        foreign_keys=[assigned_to_id]
    )
    assigned_by: Mapped["AdminUser"] = relationship(
        foreign_keys=[assigned_by_id]
    )


class Response(Base):
    """
    Official response from agency to citizen's complaint.
    """
    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id"), nullable=False, index=True)
    response_text: Mapped[str] = mapped_column(Text, nullable=False, comment="PII: response content")
    response_language: Mapped[str] = mapped_column(String(15), nullable=False)
    actions_taken: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    responded_by_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_to_citizen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    complaint: Mapped["Complaint"] = relationship(back_populates="responses")
    responded_by: Mapped["AdminUser"] = relationship(foreign_keys=[responded_by_id])
    approved_by: Mapped[Optional["AdminUser"]] = relationship(foreign_keys=[approved_by_id])


class Deadline(Base):
    """
    Deadline tracking for complaint resolution.
    Deadlines are configurable, not hardcoded.
    """
    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), unique=True, nullable=False
    )
    original_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    warning_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    exceeded_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    complaint: Mapped["Complaint"] = relationship(back_populates="deadline")
    extensions: Mapped[list["DeadlineExtension"]] = relationship(back_populates="deadline")


class DeadlineExtension(Base):
    """
    Record of deadline extensions.
    Each extension requires: approver, reason, notification to citizen.
    """
    __tablename__ = "deadline_extensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deadline_id: Mapped[int] = mapped_column(ForeignKey("deadlines.id"), nullable=False)
    previous_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    new_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    citizen_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    citizen_notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    deadline: Mapped["Deadline"] = relationship(back_populates="extensions")
    approved_by: Mapped["AdminUser"] = relationship()


class Notification(Base):
    """Telegram notification tracking with retry support."""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("complaints.id"), nullable=True, index=True
    )
    recipient_telegram_id: Mapped[int] = mapped_column(Integer, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(15), nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    complaint: Mapped[Optional["Complaint"]] = relationship(back_populates="notifications")


class AdminUser(Base):
    """
    Admin panel user with role-based access.
    Authentication via bcrypt-hashed password.
    """
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    telegram_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True, nullable=True)
    organization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="MFA ready but not implemented yet"
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    organization: Mapped[Optional["Organization"]] = relationship(back_populates="admin_users")
    sessions: Mapped[list["AdminSession"]] = relationship(back_populates="admin_user")


class AdminSession(Base):
    """Admin panel session tracking."""
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    admin_user: Mapped["AdminUser"] = relationship(back_populates="sessions")


class AuditEvent(Base):
    """
    Immutable audit trail.
    CRITICAL: This table is APPEND-ONLY.
    No UPDATE or DELETE operations should ever be performed.
    """
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="complaint/admin_user/category/etc."
    )
    entity_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="system/citizen/admin"
    )
    actor_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="JSON of old state")
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="JSON of new state")
    metadata_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Additional context as JSON"
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_actor", "actor_type", "actor_id"),
        Index("ix_audit_events_created", "created_at"),
    )


class SystemConfig(Base):
    """
    System-wide configuration stored in database.
    Allows admin to change settings without redeployment.
    """
    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RateLimit(Base):
    """Rate limiting tracker per Telegram user."""
    __tablename__ = "rate_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        UniqueConstraint("telegram_id", "action", "window_start", name="uq_rate_limit"),
    )
