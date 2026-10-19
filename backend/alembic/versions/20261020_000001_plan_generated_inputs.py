"""what a plan was last generated from

ai-trainer-ops#158. The dashboard offers to rebuild the plan once the coach
knows something that matters, which needs what the plan was built from.
Plans from before have none and are not offered a rebuild until the next
generation records it.

Revision ID: 20261020_000001
Revises: 20261019_000002
Create Date: 2026-10-20 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261020_000001"
down_revision = "20261019_000002"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("training_plans")}


def upgrade() -> None:
    if "generated_inputs" not in _columns():
        op.add_column("training_plans", sa.Column("generated_inputs", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "generated_inputs" in _columns():
        with op.batch_alter_table("training_plans") as batch:
            batch.drop_column("generated_inputs")
