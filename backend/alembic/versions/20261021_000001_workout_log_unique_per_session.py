"""one workout log per session, not per day

ai-trainer-ops#174. The initial schema made ``workout_logs`` unique on
``(user_id, date)``. Two-a-days (#496, 20260802_000001) added ``slot`` but kept
that constraint, so on production a second session on a day, such as a planned
session next to an extra one (slot 100 and up), was a unique violation and a 500.
The model declared no constraint, so the tests never saw it.

The key becomes ``(user_id, date, slot)``. That only widens it, so no stored row
conflicts. The downgrade narrows it again and stops while a day holds more than
one log, rather than delete one of them.

Revision ID: 20261021_000001
Revises: 20261020_000003
Create Date: 2026-10-21 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261021_000001"
down_revision = "20261020_000003"
branch_labels = None
depends_on = None

PER_DAY = ["user_id", "date"]
PER_SESSION = ["user_id", "date", "slot"]
# The name Postgres gave the initial schema's unnamed constraint.
PER_DAY_NAME = "workout_logs_user_id_date_key"
PER_SESSION_NAME = "uq_workout_logs_user_date_slot"
# SQLite keeps an unnamed constraint unnamed. Batch mode names it with this
# convention, so it can be dropped by that name.
NAMING = {"uq": "uq_%(table_name)s_%(column_0_N_name)s"}


def _unique_on(columns: list[str]) -> list[str]:
    """The names of the unique constraints on exactly ``columns``. An unnamed one
    gets the name batch mode gives it by ``NAMING``."""
    unnamed = f"uq_workout_logs_{'_'.join(columns)}"
    return [
        c["name"] or unnamed
        for c in sa_inspect(op.get_bind()).get_unique_constraints("workout_logs")
        if c["column_names"] == columns
    ]


def upgrade() -> None:
    per_day = _unique_on(PER_DAY)
    per_session = _unique_on(PER_SESSION)
    if not per_day and per_session:
        return
    with op.batch_alter_table("workout_logs", naming_convention=NAMING) as batch:
        for name in per_day:
            batch.drop_constraint(name, type_="unique")
        if not per_session:
            batch.create_unique_constraint(PER_SESSION_NAME, PER_SESSION)


def downgrade() -> None:
    doubled = op.get_bind().execute(
        sa.text(
            "SELECT user_id, date FROM workout_logs "
            "GROUP BY user_id, date HAVING count(*) > 1"
        )
    ).fetchall()
    if doubled:
        raise RuntimeError(
            "Cannot make workout_logs unique per day again: "
            f"{len(doubled)} day(s) hold more than one logged session, and "
            "one of each would have to be deleted first."
        )
    per_day = _unique_on(PER_DAY)
    with op.batch_alter_table("workout_logs", naming_convention=NAMING) as batch:
        for name in _unique_on(PER_SESSION):
            batch.drop_constraint(name, type_="unique")
        if not per_day:
            batch.create_unique_constraint(PER_DAY_NAME, PER_DAY)
