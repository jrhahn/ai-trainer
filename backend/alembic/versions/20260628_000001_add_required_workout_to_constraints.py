"""add required_workout to athlete_availability_constraints

Revision ID: 20260628_000001
Revises: 20260623_000001
Create Date: 2026-06-28 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260628_000001"
down_revision = "20260623_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {
        c["name"] for c in inspector.get_columns("athlete_availability_constraints")
    }
    if "required_workout" not in columns:
        op.add_column(
            "athlete_availability_constraints",
            sa.Column("required_workout", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {
        c["name"] for c in inspector.get_columns("athlete_availability_constraints")
    }
    if "required_workout" in columns:
        op.drop_column("athlete_availability_constraints", "required_workout")
