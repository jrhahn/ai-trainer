"""add label_override to ride_metrics

Revision ID: 20260530_000001
Revises: 20260524_000001
Create Date: 2026-05-30 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260530_000001"
down_revision = "20260524_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}

    if "label_override" not in existing_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("label_override", sa.String(50), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("ride_metrics", "label_override")
