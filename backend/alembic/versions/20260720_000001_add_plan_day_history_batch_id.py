"""add batch_id to plan_day_history (#435: group a coach run's per-day rows)

Revision ID: 20260720_000001
Revises: 20260712_000001
Create Date: 2026-07-20 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260720_000001"
down_revision = "20260712_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("plan_day_history")}
    if "batch_id" not in existing_columns:
        # Nullable: rows written before this column existed have no run id and
        # simply fall back to per-row grouping in the timeline.
        op.add_column(
            "plan_day_history",
            sa.Column("batch_id", sa.String(length=36), nullable=True),
        )
    existing_indexes = {
        ix["name"] for ix in inspector.get_indexes("plan_day_history")
    }
    if "ix_plan_day_history_batch_id" not in existing_indexes:
        op.create_index(
            "ix_plan_day_history_batch_id", "plan_day_history", ["batch_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_indexes = {
        ix["name"] for ix in inspector.get_indexes("plan_day_history")
    }
    if "ix_plan_day_history_batch_id" in existing_indexes:
        op.drop_index("ix_plan_day_history_batch_id", table_name="plan_day_history")
    existing_columns = {c["name"] for c in inspector.get_columns("plan_day_history")}
    if "batch_id" in existing_columns:
        op.drop_column("plan_day_history", "batch_id")
