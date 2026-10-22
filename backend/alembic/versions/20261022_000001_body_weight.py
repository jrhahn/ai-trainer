"""optional body weight in the profile

ai-trainer-ops#188. A climb power target depends on W/kg, and the profile had no
weight. The athlete enters it in Settings, if at all.

Revision ID: 20261022_000001
Revises: 20261021_000003
Create Date: 2026-10-22 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261022_000001"
down_revision = "20261021_000003"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "weight_kg" not in _columns():
        op.add_column("users", sa.Column("weight_kg", sa.Float(), nullable=True))


def downgrade() -> None:
    if "weight_kg" in _columns():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("weight_kg")
