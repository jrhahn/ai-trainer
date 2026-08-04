"""add feel_legs to ride_metrics

Revision ID: 20260705_000001
Revises: 20260701_000001
Create Date: 2026-07-05 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260705_000001"
down_revision = "20260701_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "feel_legs" not in existing_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("feel_legs", sa.String(10), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "feel_legs" in existing_columns:
        op.drop_column("ride_metrics", "feel_legs")
