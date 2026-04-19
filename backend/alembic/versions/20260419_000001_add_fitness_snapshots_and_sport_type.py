"""add sport_type to workout_logs for multi-sport .fit file support

Revision ID: 20260419_000001
Revises: 20260418_000002
Create Date: 2026-04-19 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260419_000001"
down_revision = "20260418_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    # --- sport_type column on workout_logs ---
    existing_columns = {c["name"] for c in inspector.get_columns("workout_logs")}
    if "sport_type" not in existing_columns:
        op.add_column(
            "workout_logs",
            sa.Column("sport_type", sa.String(length=50), nullable=False, server_default="cycling"),
        )


def downgrade() -> None:
    op.drop_column("workout_logs", "sport_type")
