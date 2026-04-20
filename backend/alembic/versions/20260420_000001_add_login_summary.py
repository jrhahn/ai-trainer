"""add login_summary to rider_assessments

Revision ID: 20260420_000001
Revises: 20260419_000001
Create Date: 2026-04-20 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260420_000001"
down_revision = "20260419_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("rider_assessments")}
    if "login_summary" not in existing_columns:
        op.add_column(
            "rider_assessments",
            sa.Column("login_summary", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("rider_assessments", "login_summary")
