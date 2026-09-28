"""Encrypt identity fields and widen their storage for ciphertext.

Revision ID: b53ea73c8d11
Revises: 7a091d6b8e40
"""

from alembic import op
import sqlalchemy as sa

from app.pii_crypto import encrypt_identity


revision = "b53ea73c8d11"
down_revision = "7a091d6b8e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        for column, old_type in (("birth_date", sa.String(10)), ("passport_data", sa.String(20))):
            op.alter_column("complaints", column, existing_type=old_type, type_=sa.Text(), existing_nullable=True)

    rows = list(connection.execute(sa.text(
        "SELECT id, birth_date, passport_data FROM complaints "
        "WHERE birth_date IS NOT NULL OR passport_data IS NOT NULL"
    )).mappings())
    for row in rows:
        updates = {}
        for column in ("birth_date", "passport_data"):
            value = row[column]
            if value is not None and not value.startswith("enc:v1:"):
                updates[column] = encrypt_identity(value)
        if updates:
            connection.execute(
                sa.text("UPDATE complaints SET birth_date = COALESCE(:birth_date, birth_date), "
                        "passport_data = COALESCE(:passport_data, passport_data) WHERE id = :id"),
                {"id": row["id"], "birth_date": updates.get("birth_date"),
                 "passport_data": updates.get("passport_data")},
            )


def downgrade() -> None:
    raise RuntimeError("Identity-field encryption cannot be safely downgraded")
