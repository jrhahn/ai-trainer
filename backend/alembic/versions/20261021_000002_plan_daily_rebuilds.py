"""how often the plan was rebuilt on the athlete's day

ai-trainer-ops#175. A full rebuild is offered after every new race or answer,
and the server allows a handful a day, counted on the plan.

Revision ID: 20261021_000002
Revises: 20261021_000001
Create Date: 2026-10-21 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261021_000002"
down_revision = "20261021_000001"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("rebuild_day", sa.String(10)),
    ("rebuild_count", sa.Integer()),
)


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("training_plans")}


def upgrade() -> None:
    present = _columns()
    for name, type_ in _COLUMNS:
        if name not in present:
            op.add_column("training_plans", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    present = _columns()
    drop = [name for name, _ in _COLUMNS if name in present]
    if drop:
        with op.batch_alter_table("training_plans") as batch:
            for name in drop:
                batch.drop_column(name)
