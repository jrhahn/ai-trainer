"""add coach_reviewed_at to ride_metrics

Revision ID: 20260503_000002
Revises: 20260503_000001
Create Date: 2026-05-03 00:00:02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260503_000002"
down_revision = "20260503_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "coach_reviewed_at" not in existing_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("coach_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "coach_reviewed_at" in existing_columns:
        op.drop_column("ride_metrics", "coach_reviewed_at")
