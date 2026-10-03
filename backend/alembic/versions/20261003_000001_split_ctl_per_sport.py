"""keep one CTL per sport and one ATL across all of them

CTL and ATL were a single pair of numbers for the whole athlete, built from
cycling TSS. Once a run and a gym hour carry a load too (#712), that pair claims
something false: that a marathon made the athlete better at cycling. Fitness is
sport-specific and fatigue is not, so #713 splits them — a CTL per sport, one
aggregate ATL, and TSB per sport as ``that sport's CTL − the aggregate ATL``.

This migration does two things:

1. adds ``ctl_by_sport`` to ``ride_metrics`` and ``athlete_metric_snapshots`` —
   the whole ledger, because ``ctl_after`` can only hold the rung of the sport
   that happened to be logged, and a cycling plan projection on a day the
   athlete ran has to be able to ask for cycling;
2. replays each athlete's chain per-sport from the loads and sport types already
   stored, for the same reason as 20260815 and 20260818: a value computed under
   the old rule invalidates every chain value after it.

A single-sport history comes out of the replay bit-identical — with one sport
there is nothing else to decay, so the ledger reduces to the old recurrence.
Mixed histories change, which is the point: the cycling CTL stops counting
strength and running load as cycling fitness, and the ATL keeps all of it.

``athlete_metric_snapshots`` rows are left with a NULL ``ctl_by_sport`` rather
than backfilled. They are derived views of the ride chain taken at a moment, not
a source of truth, and ``fitness_ledger.ledger_from_row`` reads a NULL ledger as
cycling-only — which is exactly what the number meant when it was written. The
next recalculation writes the real ledger.

Idempotent: the replay derives the same ledger from the same stored loads.

The replay imports the **live** ledger rather than freezing a private copy of
it, which is a decision with an obligation attached. Freezing would make this
revision replay a fresh database under a rule the app no longer uses — values the
running code would never produce — and that is worse than the inconsistency it
avoids. The repository's answer to a changed load rule is a new replay revision:
20260818_000001 exists precisely because ``DEFAULT_LOAD_PER_HOUR`` moved and
stored history had to follow. 20260815_000001 and 20260818_000001 both import
live code for the same reason.

So: **if ``ledger_sport`` or the ledger's decay rule changes (#714, #716, or the
cross-sport transfer work), that change needs its own replay revision**, exactly
as the constants change did. Without one, an already-migrated database keeps the
old rule's values while a fresh one gets the new rule's, and nothing corrects
either.

Revision ID: 20261003_000001
Revises: 20261001_000001
Create Date: 2026-10-03 00:00:01
"""

from __future__ import annotations

from datetime import date

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from services.activity_identity import SPORT_CYCLING
from services.fitness_ledger import LoadLedger, ledger_sport


revision = "20261003_000001"
down_revision = "20261001_000001"
branch_labels = None
depends_on = None


_SELECT = sa.text(
    "SELECT id, user_id, activity_date, sport_type, tss "
    "FROM ride_metrics ORDER BY user_id, activity_date, id"
)

_UPDATE_CHAIN = sa.text(
    "UPDATE ride_metrics SET ctl_after = :ctl, atl_after = :atl, tsb_after = :tsb, "
    "ctl_by_sport = :ctl_by_sport WHERE id = :row_id"
).bindparams(sa.bindparam("ctl_by_sport", type_=sa.JSON()))

_UPDATE_CHAIN_SINGLE = sa.text(
    "UPDATE ride_metrics SET ctl_after = :ctl, atl_after = :atl, tsb_after = :tsb "
    "WHERE id = :row_id"
)


def _table_exists(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    return column in {col["name"] for col in inspector.get_columns(table)}


def _days_between(current: str, previous: str | None) -> int:
    """Calendar days from *previous* to *current*, or 1 when undecidable.

    ``0`` is a real answer — the second session of a two-a-day — and the ledger
    needs to see it, because decaying every other sport for a day that never
    elapsed is exactly the drift this split could introduce.
    """
    if previous is None:
        return 1
    try:
        return max(0, (date.fromisoformat(current) - date.fromisoformat(previous)).days)
    except ValueError:
        return 1


def _replay(bind, *, per_sport: bool) -> None:
    """Rebuild the chain per athlete, in date order.

    Mirrors ``build_ride_metrics_chain`` by using the same ledger it does, so
    this cannot be a second copy of the rule that drifts from it. With
    ``per_sport=False`` every session is booked to the cycling rung, which
    reproduces the pre-#713 single-pair recurrence for :func:`downgrade` — one
    sport means nothing else to decay, and cycling is how that one number was
    read everywhere.
    """
    ledger = LoadLedger()
    current_user: str | None = None
    prev_date: str | None = None
    statement = _UPDATE_CHAIN if per_sport else _UPDATE_CHAIN_SINGLE

    for row in bind.execute(_SELECT):
        if row.user_id != current_user:
            current_user, ledger, prev_date = row.user_id, LoadLedger(), None

        sport = ledger_sport(row.sport_type) if per_sport else SPORT_CYCLING
        ledger = ledger.advance(
            sport=sport,
            load=float(row.tss) if row.tss is not None else 0.0,
            days_since_previous=_days_between(row.activity_date, prev_date),
        )
        prev_date = row.activity_date

        params = {
            "row_id": row.id,
            "ctl": round(ledger.ctl(sport), 2),
            "atl": round(ledger.atl, 2),
            "tsb": round(ledger.tsb(sport), 2),
        }
        if per_sport:
            params["ctl_by_sport"] = ledger.as_dict()
        bind.execute(statement, params)


def upgrade() -> None:
    if not _table_exists("ride_metrics"):
        return

    if not _column_exists("ride_metrics", "ctl_by_sport"):
        op.add_column("ride_metrics", sa.Column("ctl_by_sport", sa.JSON(), nullable=True))
    if _table_exists("athlete_metric_snapshots") and not _column_exists(
        "athlete_metric_snapshots", "ctl_by_sport"
    ):
        op.add_column(
            "athlete_metric_snapshots",
            sa.Column("ctl_by_sport", sa.JSON(), nullable=True),
        )

    _replay(op.get_bind(), per_sport=True)


def downgrade() -> None:
    if not _table_exists("ride_metrics"):
        return
    if not _column_exists("ride_metrics", "ctl_by_sport"):
        return

    # Put the one-pair chain back before dropping the column that explains the
    # split, so history is not left half-migrated under a schema that can no
    # longer express it.
    _replay(op.get_bind(), per_sport=False)

    op.drop_column("ride_metrics", "ctl_by_sport")
    if _table_exists("athlete_metric_snapshots") and _column_exists(
        "athlete_metric_snapshots", "ctl_by_sport"
    ):
        op.drop_column("athlete_metric_snapshots", "ctl_by_sport")
