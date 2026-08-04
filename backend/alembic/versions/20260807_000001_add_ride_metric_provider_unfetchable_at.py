"""add provider_unfetchable_at to ride_metrics

Revision ID: 20260807_000001
Revises: 20260806_000001
Create Date: 2026-08-04 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260807_000001"
down_revision = "20260806_000001"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "provider_unfetchable_at" in _column_names("ride_metrics"):
        return
    op.add_column(
        "ride_metrics",
        sa.Column(
            "provider_unfetchable_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    if "provider_unfetchable_at" not in _column_names("ride_metrics"):
        return
    op.drop_column("ride_metrics", "provider_unfetchable_at")
