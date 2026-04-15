"""initial schema

Revision ID: 20260410_000001
Revises: 
Create Date: 2026-04-10 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260410_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guard against running on a database that was bootstrapped via
    # SQLAlchemy's create_all (no alembic_version tracking). Each table is
    # only created when it does not already exist.
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing = set(inspector.get_table_names())

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("hashed_password", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("bike_type", sa.String(length=50), nullable=True),
            sa.Column("training_goal", sa.String(length=50), nullable=True),
            sa.Column("race_date", sa.String(length=20), nullable=True),
            sa.Column("race_description", sa.Text(), nullable=True),
            sa.Column("weekly_hours", sa.Float(), nullable=True),
            sa.Column("follows_training_plan", sa.Boolean(), nullable=False),
            sa.Column("resting_heart_rate", sa.Integer(), nullable=True),
            sa.Column("max_heart_rate", sa.Integer(), nullable=True),
            sa.Column("current_ftp", sa.Integer(), nullable=True),
            sa.Column("fitness_level", sa.String(length=50), nullable=True),
            sa.Column("ai_provider", sa.String(length=20), nullable=False),
            sa.Column("strava_analysis_complete", sa.Boolean(), nullable=False),
            sa.Column("is_onboarded", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    if "training_plans" not in existing:
        op.create_table(
            "training_plans",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("plan", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )

    if "workout_logs" not in existing:
        op.create_table(
            "workout_logs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("date", sa.String(length=20), nullable=False),
            sa.Column("actual_duration_minutes", sa.Integer(), nullable=False),
            sa.Column("average_power", sa.Integer(), nullable=True),
            sa.Column("average_heart_rate", sa.Integer(), nullable=True),
            sa.Column("peak_power", sa.Integer(), nullable=True),
            sa.Column("perceived_effort", sa.Integer(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("completed_at", sa.String(length=50), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "date"),
        )
        op.create_index(op.f("ix_workout_logs_date"), "workout_logs", ["date"], unique=False)

    if "chat_messages" not in existing:
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("timestamp", sa.String(length=50), nullable=False),
            sa.Column("plan_update_count", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "coach_memory" not in existing:
        op.create_table(
            "coach_memory",
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("memory", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("user_id"),
        )

    if "strava_tokens" not in existing:
        op.create_table(
            "strava_tokens",
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("access_token", sa.String(length=255), nullable=False),
            sa.Column("refresh_token", sa.String(length=255), nullable=False),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
            sa.Column("athlete_id", sa.BigInteger(), nullable=False),
            sa.Column("athlete_name", sa.String(length=255), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("user_id"),
        )

    if "rider_assessments" not in existing:
        op.create_table(
            "rider_assessments",
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("estimated_ftp", sa.Integer(), nullable=True),
            sa.Column("estimated_threshold_hr", sa.Integer(), nullable=True),
            sa.Column("rider_type", sa.String(length=50), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("user_id"),
        )


def downgrade() -> None:
    op.drop_table("rider_assessments")
    op.drop_table("strava_tokens")
    op.drop_table("coach_memory")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_workout_logs_date"), table_name="workout_logs")
    op.drop_table("workout_logs")
    op.drop_table("training_plans")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
