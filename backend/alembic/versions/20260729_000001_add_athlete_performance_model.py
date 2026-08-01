"""add athlete performance model tables and ride_metrics.perf_signals

Revision ID: 20260729_000001
Revises: 20260723_000001
Create Date: 2026-07-29 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260729_000001"
down_revision = "20260723_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "athlete_performance_models" not in tables:
        op.create_table(
            "athlete_performance_models",
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"),
                      primary_key=True),
            sa.Column("attributes", sa.JSON(), nullable=False),
            sa.Column("likely_limiter", sa.String(50), nullable=True),
            sa.Column("source_window_days", sa.Integer(), nullable=True),
            sa.Column("derived_from_rides", sa.Integer(), nullable=False,
                      server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "athlete_performance_snapshots" not in tables:
        op.create_table(
            "athlete_performance_snapshots",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"),
                      nullable=False, index=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attributes", sa.JSON(), nullable=False),
            sa.Column("likely_limiter", sa.String(50), nullable=True),
        )

    ride_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "perf_signals" not in ride_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("perf_signals", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    ride_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "perf_signals" in ride_columns:
        op.drop_column("ride_metrics", "perf_signals")

    tables = set(inspector.get_table_names())
    if "athlete_performance_snapshots" in tables:
        op.drop_table("athlete_performance_snapshots")
    if "athlete_performance_models" in tables:
        op.drop_table("athlete_performance_models")
