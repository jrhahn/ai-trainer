"""the sports and weekdays an athlete trains, asked at onboarding

ai-trainer-ops#43. Onboarding never asked which sports to plan or which days
the athlete can train, so the first plan was built blind. Both are JSON lists
of fixed choices. NULL means not stated, which every account created before
this migration is.

Revision ID: 20261012_000001
Revises: 20261011_000001
Create Date: 2026-10-12 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20261012_000001"
down_revision = "20261011_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}
    for name in ("sports", "training_days"):
        if name not in existing:
            op.add_column("users", sa.Column(name, sa.JSON(), nullable=True))


def downgrade() -> None:
    existing = {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}
    for name in ("training_days", "sports"):
        if name in existing:
            op.drop_column("users", name)
