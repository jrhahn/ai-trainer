"""give a gym session its own structure: exercises, sets, reps, load

A cyclist who lifts has been invisible to this app's numbers beyond a quantity
of fatigue (#579, #713). #714 makes the session first-class in the gym's own
units — tonnage and e1RM — and those need the sets.

Rows rather than a JSON blob on ``workout_logs``, because the feature is "e1RM
trends over time, per exercise", which is a query. The index on
``(user_id, exercise, date)`` is the one that serves it; a blob would make
reading six months of squat progression a scan and a parse.

Identity is ``(user_id, date, slot, exercise, set_index)``. The session is still
``(date, slot)`` — the identity #496 established for two-a-days — and the
session-level facts (duration, session-RPE, notes) stay on ``workout_logs``
rather than being copied here, because a gym session's perceived effort is what
its *load* is derived from and two places to store it is two places to disagree.

No derived columns. Tonnage, e1RM and %e1RM are computed by
``services.strength_model`` from these rows, so correcting a formula corrects
every historical figure instead of leaving stored values behind it.

Revision ID: 20261004_000001
Revises: 20261003_000001
Create Date: 2026-10-04 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20261004_000001"
down_revision = "20261003_000001"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _table_exists("strength_sets"):
        return

    op.create_table(
        "strength_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.String(length=20), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exercise", sa.String(length=100), nullable=False),
        sa.Column("set_index", sa.Integer(), nullable=False),
        sa.Column("reps", sa.Integer(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        # Nullable, and nullable *per set*: nobody annotates the warm-ups, and a
        # NULL here is read as missing rather than as "taken to failure".
        sa.Column("rir", sa.Integer(), nullable=True),
        sa.Column("rpe", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "date",
            "slot",
            "exercise",
            "set_index",
            name="uq_strength_sets_identity",
        ),
    )
    op.create_index(
        "ix_strength_sets_user_id", "strength_sets", ["user_id"], unique=False
    )
    op.create_index("ix_strength_sets_date", "strength_sets", ["date"], unique=False)
    # The index the per-exercise trend is read through.
    op.create_index(
        "ix_strength_sets_user_exercise_date",
        "strength_sets",
        ["user_id", "exercise", "date"],
        unique=False,
    )


def downgrade() -> None:
    if not _table_exists("strength_sets"):
        return
    op.drop_index("ix_strength_sets_user_exercise_date", table_name="strength_sets")
    op.drop_index("ix_strength_sets_date", table_name="strength_sets")
    op.drop_index("ix_strength_sets_user_id", table_name="strength_sets")
    op.drop_table("strength_sets")
