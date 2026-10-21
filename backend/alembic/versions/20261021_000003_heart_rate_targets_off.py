"""heart-rate targets off while a medication blunts the heart rate

ai-trainer-ops#184. A beta blocker said in chat turns heart-rate targets off for
every plan write; only the flag is stored.

Revision ID: 20261021_000003
Revises: 20261021_000002
Create Date: 2026-10-21 00:00:03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261021_000003"
down_revision = "20261021_000002"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "heart_rate_targets_off" not in _columns():
        op.add_column(
            "users",
            sa.Column(
                "heart_rate_targets_off",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    if "heart_rate_targets_off" in _columns():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("heart_rate_targets_off")
