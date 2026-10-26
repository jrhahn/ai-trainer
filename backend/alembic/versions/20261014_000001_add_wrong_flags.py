""" "this is wrong" flags on coach replies and planned days

ai-trainer-ops#94. One tap plus an optional note, stored with what was
flagged and the deployed commit.

Revision ID: 20261014_000001
Revises: 20261012_000002
Create Date: 2026-10-14 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261014_000001"
down_revision = "20261012_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "wrong_flags" in sa_inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "wrong_flags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target", sa.String(10), nullable=False),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("git_sha", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_wrong_flags_user_id", "wrong_flags", ["user_id"])
    op.create_index("ix_wrong_flags_created_at", "wrong_flags", ["created_at"])


def downgrade() -> None:
    if "wrong_flags" in sa_inspect(op.get_bind()).get_table_names():
        op.drop_index("ix_wrong_flags_created_at", table_name="wrong_flags")
        op.drop_index("ix_wrong_flags_user_id", table_name="wrong_flags")
        op.drop_table("wrong_flags")
