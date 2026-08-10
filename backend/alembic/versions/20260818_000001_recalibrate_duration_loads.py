"""recompute the loads that were estimated from time on task alone

#579 stopped a session without a power meter counting as a rest day, but the
per-hour assumptions it shipped were too timid. Production, the day the issue was
written about: a 58 min strength session came out at 34 against an ATL of 60, so
TSB still *rose* 2,63 points across an hour of training — less than the 5,85 it
rose when the session counted as zero, but the same sign.

Erring low here means under-reporting fatigue, which is the direction that gets
someone hurt. ``training_load.DEFAULT_LOAD_PER_HOUR`` now reads strength as
roughly a tempo hour, and cycling and hiking move up with it.

Stored rows have to follow, or the constants change and history keeps the old
answer. Only rows whose load *this rule* produced are touched — ``tss_source``
is exactly the marker that makes that decidable, which is what it was added for.
A provider figure, a power figure and an HR estimate are all left alone.

CTL/ATL/TSB are replayed afterwards for the same reason as in 20260815: loads
that change invalidate every chain value after them.

Idempotent: re-running derives the same loads from the same constants.

Revision ID: 20260818_000001
Revises: 20260817_000001
Create Date: 2026-08-09 00:00:01
"""

from __future__ import annotations

from datetime import date

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from services.analysis import apply_ctl_atl_decay
from services.training_load import LOAD_SOURCE_DURATION, duration_training_load


revision = "20260818_000001"
down_revision = "20260817_000001"
branch_labels = None
depends_on = None


_SELECT = sa.text(
    "SELECT id, user_id, activity_date, sport_type, duration_seconds, tss, tss_source "
    "FROM ride_metrics ORDER BY user_id, activity_date, id"
)

_UPDATE_LOAD = sa.text("UPDATE ride_metrics SET tss = :tss WHERE id = :row_id")

_UPDATE_CHAIN = sa.text(
    "UPDATE ride_metrics SET ctl_after = :ctl, atl_after = :atl, tsb_after = :tsb "
    "WHERE id = :row_id"
)


def _table_exists(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    return column in {col["name"] for col in inspector.get_columns(table)}


def _replay_chain(bind, rows, loads: dict[str, float | None]) -> None:
    """Mirrors ``build_ride_metrics_chain``: date order, per athlete."""
    ctl = atl = 0.0
    current_user: str | None = None
    prev_date: str | None = None

    for row in rows:
        if row.user_id != current_user:
            current_user, ctl, atl, prev_date = row.user_id, 0.0, 0.0, None

        gap_days = 1
        if prev_date is not None:
            try:
                gap_days = max(
                    1,
                    (
                        date.fromisoformat(row.activity_date)
                        - date.fromisoformat(prev_date)
                    ).days,
                )
            except ValueError:
                gap_days = 1

        ctl, atl = apply_ctl_atl_decay(ctl, atl, loads.get(row.id) or 0.0, gap_days=gap_days)
        prev_date = row.activity_date
        bind.execute(
            _UPDATE_CHAIN,
            {
                "row_id": row.id,
                "ctl": round(ctl, 2),
                "atl": round(atl, 2),
                "tsb": round(ctl - atl, 2),
            },
        )


def _recompute(bind) -> None:
    rows = list(bind.execute(_SELECT))
    loads: dict[str, float | None] = {}

    for row in rows:
        if row.tss_source != LOAD_SOURCE_DURATION:
            # Measured, or estimated from heart rate. Not this rule's to change.
            loads[row.id] = float(row.tss) if row.tss is not None else None
            continue
        load = duration_training_load(
            duration_seconds=row.duration_seconds, sport_type=row.sport_type
        )
        loads[row.id] = load
        bind.execute(_UPDATE_LOAD, {"row_id": row.id, "tss": load})

    _replay_chain(bind, rows, loads)


def upgrade() -> None:
    if not _table_exists("ride_metrics"):
        return
    if not _column_exists("ride_metrics", "tss_source"):
        return
    _recompute(op.get_bind())


def downgrade() -> None:
    # Nothing to undo structurally, and the old constants are gone from the code
    # — re-deriving them here would be a second copy of a rule this migration
    # exists to make single. Re-running upgrade() against the previous
    # ``DEFAULT_LOAD_PER_HOUR`` is what a real revert would do.
    return
