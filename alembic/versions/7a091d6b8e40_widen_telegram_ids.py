"""Widen Telegram identifiers for PostgreSQL's 64-bit Bot API IDs.

Revision ID: 7a091d6b8e40
Revises: e2d53e1f10ac
"""

from alembic import op
import sqlalchemy as sa


revision = "7a091d6b8e40"
down_revision = "e2d53e1f10ac"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("users", "telegram_id", False),
    ("admin_users", "telegram_id", True),
    ("notifications", "recipient_telegram_id", False),
    ("rate_limits", "telegram_id", False),
)


def upgrade() -> None:
    # SQLite INTEGER is already signed 64-bit; PostgreSQL INTEGER is int4.
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table, column, existing_type=sa.Integer(), type_=sa.BigInteger(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table, column, existing_type=sa.BigInteger(), type_=sa.Integer(),
            existing_nullable=nullable,
        )
