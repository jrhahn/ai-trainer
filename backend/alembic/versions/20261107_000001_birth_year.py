"""optional birth year in the profile

ai-trainer-ops#182. Recovery, a maximum heart rate and the health questions an
older athlete needs depend on age, and the profile had none. The athlete enters
the year, if at all.

Revision ID: 20261107_000001
Revises: 20261104_000001
Create Date: 2026-11-07 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261107_000001"
down_revision = "20261104_000001"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "birth_year" not in _columns():
        op.add_column("users", sa.Column("birth_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    if "birth_year" in _columns():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("birth_year")
