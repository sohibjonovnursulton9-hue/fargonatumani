"""Add reversible complaint archiving.

Revision ID: f4c12a98d751
Revises: b17c4d6e8f20
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4c12a98d751"
down_revision: Union[str, Sequence[str], None] = "b17c4d6e8f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("complaints") as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("archived_by_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_complaints_archived_by_id_admin_users", "admin_users",
            ["archived_by_id"], ["id"],
        )
        batch_op.create_index("ix_complaints_archived_at", ["archived_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("complaints") as batch_op:
        batch_op.drop_index("ix_complaints_archived_at")
        batch_op.drop_constraint("fk_complaints_archived_by_id_admin_users", type_="foreignkey")
        batch_op.drop_column("archived_by_id")
        batch_op.drop_column("archived_at")
