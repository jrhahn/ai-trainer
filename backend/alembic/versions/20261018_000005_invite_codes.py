"""single-use invitation codes for a closed sign-up round

ops#108 option (b), #96: the friends round signs up by invitation only. A code
is not linked to the account it opened; only the time it was used is kept.

Revision ID: 20261018_000005
Revises: 20261018_000004
Create Date: 2026-10-18 00:00:05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261018_000005"
down_revision = "20261018_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "invite_codes" in sa_inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "invite_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("note", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    if "invite_codes" in sa_inspect(op.get_bind()).get_table_names():
        op.drop_table("invite_codes")
