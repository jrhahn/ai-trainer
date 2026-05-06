"""add ride plan matching fields

Revision ID: 20260506_000001
Revises: 20260503_000002
Create Date: 2026-05-06 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260506_000001"
down_revision = "20260503_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}

    if "activity_name" not in existing_columns:
        op.add_column("ride_metrics", sa.Column("activity_name", sa.String(255), nullable=True))
    if "activity_start_datetime" not in existing_columns:
        op.add_column("ride_metrics", sa.Column("activity_start_datetime", sa.String(50), nullable=True))
    if "plan_match_status" not in existing_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("plan_match_status", sa.String(20), nullable=False, server_default="unmatched"),
        )
        op.alter_column("ride_metrics", "plan_match_status", server_default=None)
    if "matched_plan_date" not in existing_columns:
        op.add_column("ride_metrics", sa.Column("matched_plan_date", sa.String(20), nullable=True))
    if "matched_plan_snapshot" not in existing_columns:
        op.add_column("ride_metrics", sa.Column("matched_plan_snapshot", sa.JSON(), nullable=True))
    if "matched_at" not in existing_columns:
        op.add_column("ride_metrics", sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}

    for column in (
        "matched_at",
        "matched_plan_snapshot",
        "matched_plan_date",
        "plan_match_status",
        "activity_start_datetime",
        "activity_name",
    ):
        if column in existing_columns:
            op.drop_column("ride_metrics", column)
