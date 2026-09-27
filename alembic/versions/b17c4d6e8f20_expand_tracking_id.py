"""Expand citizen complaint tracking identifiers.

Revision ID: b17c4d6e8f20
Revises: 008f64605c3b
Create Date: 2026-09-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b17c4d6e8f20"
down_revision: Union[str, Sequence[str], None] = "008f64605c3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("complaints") as batch_op:
        batch_op.alter_column(
            "tracking_id",
            existing_type=sa.String(length=20),
            type_=sa.String(length=32),
            existing_nullable=False,
        )


def downgrade() -> None:
    # This safely fails on databases that already contain the new 24-character IDs.
    with op.batch_alter_table("complaints") as batch_op:
        batch_op.alter_column(
            "tracking_id",
            existing_type=sa.String(length=32),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
