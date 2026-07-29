"""add limiters column to athlete performance model + snapshot

Revision ID: 20260730_000001
Revises: 20260729_000001
Create Date: 2026-07-30 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260730_000001"
down_revision = "20260729_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    model_cols = {c["name"] for c in inspector.get_columns("athlete_performance_models")}
    if "limiters" not in model_cols:
        op.add_column(
            "athlete_performance_models",
            sa.Column("limiters", sa.JSON(), nullable=True),
        )

    snap_cols = {
        c["name"] for c in inspector.get_columns("athlete_performance_snapshots")
    }
    if "limiters" not in snap_cols:
        op.add_column(
            "athlete_performance_snapshots",
            sa.Column("limiters", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    snap_cols = {
        c["name"] for c in inspector.get_columns("athlete_performance_snapshots")
    }
    if "limiters" in snap_cols:
        op.drop_column("athlete_performance_snapshots", "limiters")

    model_cols = {c["name"] for c in inspector.get_columns("athlete_performance_models")}
    if "limiters" in model_cols:
        op.drop_column("athlete_performance_models", "limiters")
