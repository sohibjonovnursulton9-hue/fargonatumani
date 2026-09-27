"""Add auditable supplemental information exchange.

Revision ID: 5e09b24cbfe7
Revises: 94a2e6071f3c
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5e09b24cbfe7"
down_revision: Union[str, Sequence[str], None] = "94a2e6071f3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "complaint_citizen_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("complaint_id", sa.Integer(), nullable=False),
        sa.Column("reply_to_id", sa.Integer(), nullable=True),
        sa.Column("message_type", sa.String(length=12), nullable=False),
        sa.Column("author_type", sa.String(length=12), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["complaint_id"], ["complaints.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["reply_to_id"], ["complaint_citizen_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reply_to_id", name="uq_citizen_message_reply_to"),
    )
    op.create_index(
        "ix_citizen_messages_complaint_created",
        "complaint_citizen_messages",
        ["complaint_id", "created_at"],
    )
    with op.batch_alter_table("attachments") as batch_op:
        batch_op.add_column(sa.Column("message_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_attachments_message_id_complaint_citizen_messages",
            "complaint_citizen_messages",
            ["message_id"],
            ["id"],
        )
        batch_op.create_index("ix_attachments_message_id", ["message_id"])


def downgrade() -> None:
    with op.batch_alter_table("attachments") as batch_op:
        batch_op.drop_index("ix_attachments_message_id")
        batch_op.drop_constraint(
            "fk_attachments_message_id_complaint_citizen_messages",
            type_="foreignkey",
        )
        batch_op.drop_column("message_id")
    op.drop_index(
        "ix_citizen_messages_complaint_created",
        table_name="complaint_citizen_messages",
    )
    op.drop_table("complaint_citizen_messages")
