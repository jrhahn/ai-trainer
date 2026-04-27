"""add strava import jobs

Revision ID: 20260427_000001
Revises: 20260425_000001
Create Date: 2026-04-27 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260427_000001"
down_revision = "20260425_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "strava_import_jobs" not in inspector.get_table_names():
        op.create_table(
            "strava_import_jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
            sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("imported", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_activities", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_strava_import_jobs_user_id", "strava_import_jobs", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "strava_import_jobs" in inspector.get_table_names():
        op.drop_index("ix_strava_import_jobs_user_id", table_name="strava_import_jobs")
        op.drop_table("strava_import_jobs")
