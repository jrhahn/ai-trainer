"""add ride_metrics table

Revision ID: 20260421_000001
Revises: 20260420_000001
Create Date: 2026-04-21 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260421_000001"
down_revision = "20260420_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()
    if "ride_metrics" not in existing_tables:
        op.create_table(
            "ride_metrics",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("strava_activity_id", sa.BigInteger(), nullable=False),
            sa.Column("activity_date", sa.String(20), nullable=False),
            sa.Column("sport_type", sa.String(50), nullable=False, server_default="cycling"),
            sa.Column("duration_seconds", sa.Integer(), nullable=True),
            sa.Column("avg_power_w", sa.Integer(), nullable=True),
            sa.Column("normalized_power_w", sa.Integer(), nullable=True),
            sa.Column("intensity_factor", sa.Float(), nullable=True),
            sa.Column("tss", sa.Float(), nullable=True),
            sa.Column("ftp_used", sa.Integer(), nullable=True),
            sa.Column("ctl_after", sa.Float(), nullable=True),
            sa.Column("atl_after", sa.Float(), nullable=True),
            sa.Column("tsb_after", sa.Float(), nullable=True),
            sa.Column("ride_purpose", sa.String(50), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("coach_note", sa.Text(), nullable=True),
            sa.Column("user_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_ride_metrics_user_id",
            "ride_metrics",
            ["user_id"],
        )
        op.create_index(
            "ix_ride_metrics_user_activity",
            "ride_metrics",
            ["user_id", "strava_activity_id"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("ix_ride_metrics_user_activity", table_name="ride_metrics")
    op.drop_index("ix_ride_metrics_user_id", table_name="ride_metrics")
    op.drop_table("ride_metrics")
