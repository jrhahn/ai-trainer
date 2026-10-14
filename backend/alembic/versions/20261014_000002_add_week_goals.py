"""the goal sentence of each week of the season plan

ai-trainer-ops#98. The week targets are computed on every read; only the
model's sentence is stored, with the fingerprint of the week it was written for
and of the week the nightly run last fitted the days to.

Revision ID: 20261014_000002
Revises: 20261012_000002
Create Date: 2026-10-14 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261014_000002"
down_revision = "20261012_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "week_goals" in sa_inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "week_goals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("week_start", sa.String(10), nullable=False),
        sa.Column("fingerprint", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("fitted_fingerprint", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "week_start", name="uq_week_goals_user_week"),
    )


def downgrade() -> None:
    if "week_goals" in sa_inspect(op.get_bind()).get_table_names():
        op.drop_table("week_goals")
