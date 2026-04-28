"""drop threshold hr columns

Revision ID: 20260428_000001
Revises: 20260427_000001
Create Date: 2026-04-28 00:00:01
"""

from alembic import op
from sqlalchemy import inspect as sa_inspect


revision = "20260428_000001"
down_revision = "20260427_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "threshold_heart_rate" in user_cols:
        op.drop_column("users", "threshold_heart_rate")

    ra_cols = {c["name"] for c in inspector.get_columns("rider_assessments")}
    if "estimated_threshold_hr" in ra_cols:
        op.drop_column("rider_assessments", "estimated_threshold_hr")

    snap_cols = {c["name"] for c in inspector.get_columns("athlete_metric_snapshots")}
    if "threshold_hr" in snap_cols:
        op.drop_column("athlete_metric_snapshots", "threshold_hr")


def downgrade() -> None:
    import sqlalchemy as sa

    bind = op.get_bind()
    inspector = sa_inspect(bind)

    snap_cols = {c["name"] for c in inspector.get_columns("athlete_metric_snapshots")}
    if "threshold_hr" not in snap_cols:
        op.add_column("athlete_metric_snapshots", sa.Column("threshold_hr", sa.Integer(), nullable=True))

    ra_cols = {c["name"] for c in inspector.get_columns("rider_assessments")}
    if "estimated_threshold_hr" not in ra_cols:
        op.add_column("rider_assessments", sa.Column("estimated_threshold_hr", sa.Integer(), nullable=True))

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    if "threshold_heart_rate" not in user_cols:
        op.add_column("users", sa.Column("threshold_heart_rate", sa.Integer(), nullable=True))
