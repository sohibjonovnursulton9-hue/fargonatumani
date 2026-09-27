"""Link delivery receipts to deadline extension notices.

Revision ID: 94a2e6071f3c
Revises: 81d3a7c92f10
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "94a2e6071f3c"
down_revision: Union[str, Sequence[str], None] = "81d3a7c92f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(
            sa.Column("deadline_extension_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_notifications_deadline_extension_id_deadline_extensions",
            "deadline_extensions",
            ["deadline_extension_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_notifications_deadline_extension_id", ["deadline_extension_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_index("ix_notifications_deadline_extension_id")
        batch_op.drop_constraint(
            "fk_notifications_deadline_extension_id_deadline_extensions",
            type_="foreignkey",
        )
        batch_op.drop_column("deadline_extension_id")
