"""add flagged_constraint_dates to chat_messages

Revision ID: 20260722_000001
Revises: 20260720_000001
Create Date: 2026-07-22 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260722_000001"
down_revision = "20260720_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("chat_messages")}
    if "flagged_constraint_dates" not in columns:
        op.add_column(
            "chat_messages",
            sa.Column("flagged_constraint_dates", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("chat_messages")}
    if "flagged_constraint_dates" in columns:
        op.drop_column("chat_messages", "flagged_constraint_dates")
