"""add fitness_snapshots table and sport_type to workout_logs

Revision ID: 20260419_000001
Revises: 20260418_000001
Create Date: 2026-04-19 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260419_000001"
down_revision = "20260418_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # --- fitness_snapshots table ---
    if "fitness_snapshots" not in existing_tables:
        op.create_table(
            "fitness_snapshots",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ftp", sa.Integer(), nullable=True),
            sa.Column("threshold_hr", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(length=50), nullable=False, server_default="strava_analysis"),
            sa.UniqueConstraint("user_id", "measured_at", name="uq_fitness_snapshot_user_time"),
        )
        op.create_index("ix_fitness_snapshots_user_id", "fitness_snapshots", ["user_id"])

    # --- sport_type column on workout_logs ---
    existing_columns = {c["name"] for c in inspector.get_columns("workout_logs")}
    if "sport_type" not in existing_columns:
        op.add_column(
            "workout_logs",
            sa.Column("sport_type", sa.String(length=50), nullable=False, server_default="cycling"),
        )


def downgrade() -> None:
    op.drop_index("ix_fitness_snapshots_user_id", table_name="fitness_snapshots")
    op.drop_table("fitness_snapshots")
    op.drop_column("workout_logs", "sport_type")
