"""Add versioned bot configuration and private complaint notes.

Revision ID: c91a8d6e4b27
Revises: f4c12a98d751
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c91a8d6e4b27"
down_revision: Union[str, Sequence[str], None] = "f4c12a98d751"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    def ensure_table(table_name: str, columns: set[str], create_table) -> None:
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            create_table()
            inspector = sa.inspect(bind)
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        missing = columns - existing
        if missing:
            raise RuntimeError(
                f"Cannot mark {table_name} migrated; missing columns: {sorted(missing)}"
            )

    ensure_table(
        "bot_config_revisions",
        {
            "id", "config_json", "state", "note", "created_by_id", "created_at",
            "previewed_by_id", "previewed_at", "published_by_id", "published_at",
        },
        lambda: op.create_table(
            "bot_config_revisions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("state", sa.String(length=20), nullable=False),
            sa.Column("note", sa.String(length=500), nullable=True),
            sa.Column("created_by_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("previewed_by_id", sa.Integer(), nullable=True),
            sa.Column("previewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("published_by_id", sa.Integer(), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by_id"], ["admin_users.id"]),
            sa.ForeignKeyConstraint(["previewed_by_id"], ["admin_users.id"]),
            sa.ForeignKeyConstraint(["published_by_id"], ["admin_users.id"]),
            sa.PrimaryKeyConstraint("id"),
        ),
    )
    if "ix_bot_config_revisions_state_created" not in {
        index["name"] for index in sa.inspect(bind).get_indexes("bot_config_revisions")
    }:
        op.create_index(
            "ix_bot_config_revisions_state_created", "bot_config_revisions", ["state", "created_at"]
        )

    ensure_table(
        "complaint_internal_notes",
        {"id", "complaint_id", "author_id", "body", "created_at"},
        lambda: op.create_table(
            "complaint_internal_notes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("complaint_id", sa.Integer(), nullable=False),
            sa.Column("author_id", sa.Integer(), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["complaint_id"], ["complaints.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["author_id"], ["admin_users.id"]),
            sa.PrimaryKeyConstraint("id"),
        ),
    )
    if "ix_complaint_internal_notes_complaint_created" not in {
        index["name"] for index in sa.inspect(bind).get_indexes("complaint_internal_notes")
    }:
        op.create_index(
            "ix_complaint_internal_notes_complaint_created",
            "complaint_internal_notes",
            ["complaint_id", "created_at"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_complaint_internal_notes_complaint_created", table_name="complaint_internal_notes"
    )
    op.drop_table("complaint_internal_notes")
    op.drop_index("ix_bot_config_revisions_state_created", table_name="bot_config_revisions")
    op.drop_table("bot_config_revisions")
