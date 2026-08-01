"""add map_5min to athlete_metric_snapshots

Revision ID: 20260805_000001
Revises: 20260804_000001
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260805_000001"
down_revision = "20260804_000001"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "map_5min" in _column_names("athlete_metric_snapshots"):
        return
    op.add_column(
        "athlete_metric_snapshots",
        sa.Column("map_5min", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    if "map_5min" not in _column_names("athlete_metric_snapshots"):
        return
    op.drop_column("athlete_metric_snapshots", "map_5min")
