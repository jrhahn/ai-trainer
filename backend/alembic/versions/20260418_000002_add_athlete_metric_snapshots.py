"""add athlete_metric_snapshots table

Revision ID: 20260418_000002
Revises: 20260418_000001
Create Date: 2026-04-18 00:00:02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260418_000002"
down_revision = "20260418_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()
    if "athlete_metric_snapshots" not in existing_tables:
        op.create_table(
            "athlete_metric_snapshots",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ftp", sa.Integer(), nullable=True),
            sa.Column("threshold_hr", sa.Integer(), nullable=True),
            sa.Column("ctl", sa.Float(), nullable=True),
            sa.Column("atl", sa.Float(), nullable=True),
            sa.Column("tsb", sa.Float(), nullable=True),
            sa.Column("source", sa.String(50), nullable=False, server_default="strava_analysis"),
        )
        op.create_index(
            "ix_athlete_metric_snapshots_user_id",
            "athlete_metric_snapshots",
            ["user_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_athlete_metric_snapshots_user_id", table_name="athlete_metric_snapshots")
    op.drop_table("athlete_metric_snapshots")
