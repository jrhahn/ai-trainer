"""a stored trip without the bike rules out rides only

ai-trainer-ops#136. A trip said with the bike at home was stored as "no
training" on every day, so the walks and hikes the athlete planned could not go
into the plan. Such days are now "no_riding". This moves the days already
stored: a day of a stated range whose message keeps the bike at home and names
something done instead, or does not rule out training altogether.

Revision ID: 20261118_000001
Revises: 20261107_000001
Create Date: 2026-11-18 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from domain.availability import NO_RIDING, NO_RIDING_REASON, says_rides_only

revision = "20261118_000001"
down_revision = "20261107_000001"
branch_labels = None
depends_on = None

# The reason a day of a stated range was stored with, before and after.
RANGE_REASON = "Athlete said they are away and cannot train."

_constraints = sa.table(
    "athlete_availability_constraints",
    sa.column("id", sa.String),
    sa.column("constraint_type", sa.String),
    sa.column("reason", sa.Text),
    sa.column("source", sa.Text),
)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(_constraints.c.id, _constraints.c.source).where(
            _constraints.c.constraint_type == "no_training",
            _constraints.c.reason == RANGE_REASON,
        )
    ).all()
    ids = [row.id for row in rows if says_rides_only(row.source or "")]
    if ids:
        conn.execute(
            _constraints.update()
            .where(_constraints.c.id.in_(ids))
            .values(constraint_type=NO_RIDING, reason=NO_RIDING_REASON)
        )


def downgrade() -> None:
    # The code before this revision knows no "no_riding": the day is blocked
    # again, as it was.
    op.get_bind().execute(
        _constraints.update()
        .where(_constraints.c.constraint_type == NO_RIDING)
        .values(constraint_type="no_training", reason=RANGE_REASON)
    )
