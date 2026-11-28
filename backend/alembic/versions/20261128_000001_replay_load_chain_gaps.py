"""replay the load chain: a sync forgot the days since the newest stored ride

ai-trainer-ops#342. A sync seeded the chain from the newest stored ride but not
from its date, so the first new ride was chained as if it came the day after.
The quiet days between them never decayed CTL or ATL: four days off after a
hiking week left an athlete's ATL at 53 instead of 36, and the coach prescribed
a rest day on it. The ingest now passes the date. This rewrites every stored
row's chain from the loads, in the order the replay uses, as the app's own
replay would.

Idempotent. Like 20261003_000001 it imports the live ledger. The downgrade
leaves the rows alone: the values before were wrong, not a different reading.

Revision ID: 20261128_000001
Revises: 20261118_000001
Create Date: 2026-11-28 00:00:01
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op
from domain.fitness_ledger import LoadLedger, ledger_sport

revision = "20261128_000001"
down_revision = "20261118_000001"
branch_labels = None
depends_on = None

# The order of crud.get_all_ride_metrics_ordered, which the app's replay reads.
_SELECT = sa.text(
    "SELECT id, user_id, activity_date, sport_type, tss FROM ride_metrics "
    "ORDER BY user_id, activity_date, activity_start_datetime NULLS FIRST, "
    "created_at, id"
)
_UPDATE = sa.text(
    "UPDATE ride_metrics SET ctl_after = :ctl, atl_after = :atl, tsb_after = :tsb, "
    "ctl_by_sport = :ctl_by_sport WHERE id = :row_id"
).bindparams(sa.bindparam("ctl_by_sport", type_=sa.JSON()))


def _days_between(current: str, previous: str | None) -> int:
    if previous is None:
        return 1
    try:
        return max(0, (date.fromisoformat(current) - date.fromisoformat(previous)).days)
    except ValueError:
        return 1


def upgrade() -> None:
    bind = op.get_bind()
    if "ride_metrics" not in sa_inspect(bind).get_table_names():
        return
    ledger = LoadLedger()
    current_user: str | None = None
    prev_date: str | None = None
    for row in bind.execute(_SELECT).all():
        if row.user_id != current_user:
            current_user, ledger, prev_date = row.user_id, LoadLedger(), None
        sport = ledger_sport(row.sport_type)
        ledger = ledger.advance(
            sport=sport,
            load=float(row.tss) if row.tss is not None else 0.0,
            days_since_previous=_days_between(row.activity_date, prev_date),
        )
        prev_date = row.activity_date
        bind.execute(
            _UPDATE,
            {
                "row_id": row.id,
                "ctl": round(ledger.ctl(sport), 2),
                "atl": round(ledger.atl, 2),
                "tsb": round(ledger.form(), 2),
                "ctl_by_sport": ledger.as_dict(),
            },
        )


def downgrade() -> None:
    pass
