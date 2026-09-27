"""Models for reviewable bot configuration and private staff notes."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import utcnow


class BotConfigRevision(Base):
    """Immutable configuration snapshots with explicit preview and publish steps."""

    __tablename__ = "bot_config_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    previewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"))
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_bot_config_revisions_state_created", "state", "created_at"),)


class ComplaintInternalNote(Base):
    """Private case note visible only to staff with access to the complaint."""

    __tablename__ = "complaint_internal_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_complaint_internal_notes_complaint_created", "complaint_id", "created_at"),
    )


class ComplaintCitizenMessage(Base):
    """Public staff/citizen exchange attached to a complaint."""

    __tablename__ = "complaint_citizen_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False
    )
    reply_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("complaint_citizen_messages.id"), nullable=True
    )
    message_type: Mapped[str] = mapped_column(String(12), nullable=False)
    author_type: Mapped[str] = mapped_column(String(12), nullable=False)
    author_id: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_citizen_messages_complaint_created", "complaint_id", "created_at"),
        UniqueConstraint("reply_to_id", name="uq_citizen_message_reply_to"),
    )
