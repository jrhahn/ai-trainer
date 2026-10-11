"""goals with a number on them, and the elevation and distance they are measured on

ai-trainer-ops#65. A goal such as "4000 m in one ride" was stored as free text,
and the rides it should be measured against kept neither their distance nor
their climbing: ``ride_metrics`` had duration and power, and the provider's
``distance`` and ``total_elevation_gain`` were dropped at import.

Both new ``ride_metrics`` columns are nullable with no backfill and no default.
NULL is "not reported", which is a different statement from 0 m: the rows
synced before this migration, and every gym session, have none.

Revision ID: 20261011_000001
Revises: 20261010_000001
Create Date: 2026-10-11 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20261011_000001"
down_revision = "20261010_000001"
branch_labels = None
depends_on = None


def _inspector():
    return sa_inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table)}


def upgrade() -> None:
    existing = _columns("ride_metrics")
    for name in ("distance_km", "elevation_gain_m"):
        if name not in existing:
            op.add_column("ride_metrics", sa.Column(name, sa.Float(), nullable=True))

    if "athlete_goals" in _inspector().get_table_names():
        return
    op.create_table(
        "athlete_goals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("metric", sa.String(30), nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("deadline", sa.String(10), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_athlete_goals_user_status", "athlete_goals", ["user_id", "status"]
    )


def downgrade() -> None:
    if "athlete_goals" in _inspector().get_table_names():
        op.drop_index("ix_athlete_goals_user_status", table_name="athlete_goals")
        op.drop_table("athlete_goals")
    existing = _columns("ride_metrics")
    for name in ("elevation_gain_m", "distance_km"):
        if name in existing:
            op.drop_column("ride_metrics", name)
