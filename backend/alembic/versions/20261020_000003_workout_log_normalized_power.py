"""normalized power on a logged session

ai-trainer-ops#169 and #171. An athlete without an upload still knows the
normalized power from a head unit, and with an FTP it prices the session the
way a recording's power does. Nullable: "not said" is not 0 W.

Revision ID: 20261020_000003
Revises: 20261020_000002
Create Date: 2026-10-20 00:00:03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261020_000003"
down_revision = "20261020_000002"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("workout_logs")}


def upgrade() -> None:
    if "normalized_power" not in _columns():
        op.add_column(
            "workout_logs", sa.Column("normalized_power", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    if "normalized_power" in _columns():
        with op.batch_alter_table("workout_logs") as batch:
            batch.drop_column("normalized_power")
