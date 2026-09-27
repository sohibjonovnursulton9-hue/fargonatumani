"""Merge the citizen workflow and complaint recycle-bin migration heads.

Revision ID: e2d53e1f10ac
Revises: 5e09b24cbfe7, da61b9e228af
Create Date: 2026-09-27
"""
from typing import Sequence, Union


revision: str = "e2d53e1f10ac"
down_revision: Union[str, Sequence[str], None] = (
    "5e09b24cbfe7",
    "da61b9e228af",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
