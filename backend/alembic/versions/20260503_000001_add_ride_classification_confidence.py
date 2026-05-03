"""add classification_confidence and classification_reason to ride_metrics

Revision ID: 20260503_000001
Revises: 20260429_000001
Create Date: 2026-05-03 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260503_000001"
down_revision = "20260429_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "classification_confidence" not in existing_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("classification_confidence", sa.String(10), nullable=True),
        )
    if "classification_reason" not in existing_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("classification_reason", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "classification_reason" in existing_columns:
        op.drop_column("ride_metrics", "classification_reason")
    if "classification_confidence" in existing_columns:
        op.drop_column("ride_metrics", "classification_confidence")
