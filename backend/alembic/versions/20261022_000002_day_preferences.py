"""a weekday the athlete prefers over another

ai-trainer-ops#204. "Montag passt mir meistens besser als Dienstag" left nothing
behind: the next plan used Tuesday again. The chat keeps such a preference as a
pair, and the automatic writers put the week's key session on the preferred day.

Revision ID: 20261022_000002
Revises: 20261022_000001
Create Date: 2026-10-22 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261022_000002"
down_revision = "20261022_000001"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "day_preferences" not in _columns():
        op.add_column("users", sa.Column("day_preferences", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "day_preferences" in _columns():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("day_preferences")
