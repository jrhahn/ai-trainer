"""add ride_insights to rider_assessments

Revision ID: 20260415_000003
Revises: 20260415_000002
Create Date: 2026-04-15 00:00:03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260415_000003"
down_revision = "20260415_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("rider_assessments")}
    if "ride_insights" not in existing_columns:
        op.add_column(
            "rider_assessments",
            sa.Column("ride_insights", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("rider_assessments", "ride_insights")
