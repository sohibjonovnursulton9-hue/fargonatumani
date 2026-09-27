"""Add recoverable complaint deletion fields.

Revision ID: da61b9e228af
Revises: c91a8d6e4b27
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "da61b9e228af"
down_revision: Union[str, Sequence[str], None] = "c91a8d6e4b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("complaints") as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deleted_by_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_complaints_deleted_by_id_admin_users", "admin_users",
            ["deleted_by_id"], ["id"],
        )
        batch_op.create_index("ix_complaints_deleted_at", ["deleted_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("complaints") as batch_op:
        batch_op.drop_index("ix_complaints_deleted_at")
        batch_op.drop_constraint("fk_complaints_deleted_by_id_admin_users", type_="foreignkey")
        batch_op.drop_column("deleted_by_id")
        batch_op.drop_column("deleted_at")
