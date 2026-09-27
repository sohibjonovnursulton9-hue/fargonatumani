"""Add scheduled retry time to Telegram notification outbox.

Revision ID: 81d3a7c92f10
Revises: c91a8d6e4b27
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "81d3a7c92f10"
down_revision: Union[str, Sequence[str], None] = "c91a8d6e4b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_notifications_next_retry_at", "notifications", ["next_retry_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_next_retry_at", table_name="notifications")
    op.drop_column("notifications", "next_retry_at")
