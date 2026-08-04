"""narrate coach plan changes: add plan_day_history.reason + plan_change_summary (#439)

Revision ID: 20260723_000001
Revises: 20260722_000001
Create Date: 2026-07-23 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260723_000001"
down_revision = "20260722_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    # Per-day coach rationale, backfilled by the narrator. Nullable so existing
    # rows and un-narrated triggers keep working.
    columns = {c["name"] for c in inspector.get_columns("plan_day_history")}
    if "reason" not in columns:
        op.add_column(
            "plan_day_history",
            sa.Column("reason", sa.Text(), nullable=True),
        )

    # One narrative per automated coach run, keyed by the run's batch_id.
    if not inspector.has_table("plan_change_summary"):
        op.create_table(
            "plan_change_summary",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("batch_id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column(
                "recorded_at", sa.DateTime(timezone=True), nullable=True
            ),
        )
        op.create_index(
            "ix_plan_change_summary_user_id", "plan_change_summary", ["user_id"]
        )
        op.create_index(
            "ix_plan_change_summary_batch_id",
            "plan_change_summary",
            ["batch_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    if inspector.has_table("plan_change_summary"):
        op.drop_index(
            "ix_plan_change_summary_batch_id", table_name="plan_change_summary"
        )
        op.drop_index(
            "ix_plan_change_summary_user_id", table_name="plan_change_summary"
        )
        op.drop_table("plan_change_summary")

    columns = {c["name"] for c in inspector.get_columns("plan_day_history")}
    if "reason" in columns:
        op.drop_column("plan_day_history", "reason")
