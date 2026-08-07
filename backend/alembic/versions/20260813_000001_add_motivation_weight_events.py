"""add an audit trail for utility weight changes

The weights decide which training option wins (#564) and the athlete can see
them in settings (#567), so a weight that changed on its own has to be able to
say why. Until now the only trace was a log line — not queryable, not per
athlete, and gone by the time anyone asked.

One row per effective change, written at the same gate the weights are written
at, which is what makes it impossible to move a weight without leaving a trace.
Append-only and never read on the hot path.

Revision ID: 20260813_000001
Revises: 20260812_000001
Create Date: 2026-08-13 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "20260813_000001"
down_revision = "20260812_000001"
branch_labels = None
depends_on = None

_TABLE = "athlete_motivation_weight_events"


def _has_table(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("weights_before", sa.JSON(), nullable=False),
        sa.Column("weights_after", sa.JSON(), nullable=False),
        sa.Column("deltas", sa.JSON(), nullable=False),
        # Null for an athlete edit: nothing was inferred, they simply said so.
        sa.Column("rules", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("pinned", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        f"ix_{_TABLE}_user_id", _TABLE, ["user_id"]
    )
    # The history is always read newest-first for one athlete.
    op.create_index(
        f"ix_{_TABLE}_recorded_at", _TABLE, ["recorded_at"]
    )


def downgrade() -> None:
    if not _has_table(_TABLE):
        return
    op.drop_index(f"ix_{_TABLE}_recorded_at", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
