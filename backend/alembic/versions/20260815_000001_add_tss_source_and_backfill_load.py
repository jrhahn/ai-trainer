"""record where every training load came from, and give the loadless ones one

A session with no power meter stored ``tss = NULL``, and the fitness/fatigue
chain read NULL as ``0.0`` — a rest day. On prod that is 196 strength sessions,
122 mountain-bike rides, 27 yoga classes and 23 hikes, and the visible effect is
not a gap but a sign error: an hour of strength training made the app report the
athlete 5,85 TSB points *fresher* (#579).

This migration does three things, in order:

1. adds ``ride_metrics.tss_source`` — a load whose origin is not recorded cannot
   be reasoned about later, and the coach must never report rising *cycling*
   form on the back of gym work;
2. labels the loads already stored, and derives one for the rows that have none,
   using the same ladder the app now uses (``services.training_load``) — heart
   rate where the stored ``perf_signals`` kept an average, otherwise time on
   task;
3. recomputes CTL/ATL/TSB over each athlete's history, because loads that were
   zero and no longer are invalidate every chain value after them.

Idempotent: re-running derives the same loads and recomputes the same chain.

Revision ID: 20260815_000001
Revises: 20260814_000001
Create Date: 2026-08-08 00:00:01
"""

from __future__ import annotations

import json
from datetime import date

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from services.analysis import apply_ctl_atl_decay
from services.training_load import (
    LOAD_SOURCE_POWER,
    LOAD_SOURCE_PROVIDER,
    MEASURED_LOAD_SOURCES,
    resolve_training_load,
)


revision = "20260815_000001"
down_revision = "20260814_000001"
branch_labels = None
depends_on = None


_SELECT_METRICS = sa.text(
    "SELECT id, user_id, activity_date, sport_type, duration_seconds, "
    "normalized_power_w, tss, tss_source, perf_signals "
    "FROM ride_metrics ORDER BY user_id, activity_date, id"
)

_SELECT_USERS = sa.text("SELECT id, max_heart_rate, resting_heart_rate FROM users")

_UPDATE_LOAD = sa.text(
    "UPDATE ride_metrics SET tss = :tss, tss_source = :tss_source WHERE id = :row_id"
)

_UPDATE_CHAIN = sa.text(
    "UPDATE ride_metrics SET ctl_after = :ctl, atl_after = :atl, tsb_after = :tsb "
    "WHERE id = :row_id"
)


def _table_exists(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    return column in {col["name"] for col in inspector.get_columns(table)}


def _avg_hr(perf_signals: object) -> float | None:
    """Pull the stored average HR out of ``perf_signals``.

    Raw SQL hands back a dict on PostgreSQL and a JSON string on SQLite; both
    reach this migration, so both are handled here rather than at four call
    sites.
    """
    if isinstance(perf_signals, str):
        try:
            perf_signals = json.loads(perf_signals)
        except ValueError:
            return None
    if not isinstance(perf_signals, dict):
        return None
    value = perf_signals.get("avg_hr_bpm")
    return float(value) if isinstance(value, (int, float)) else None


def _existing_load_source(row: sa.engine.Row) -> str:
    """Label a load that was already stored.

    ``power`` requires a normalized power to have been computed from; without
    one the chain could not have produced the figure itself, so it came from the
    provider. Both are power-derived measurements either way — the distinction
    that matters downstream is measured versus estimated, and this gets that
    right for every row.
    """
    if row.normalized_power_w is not None:
        return LOAD_SOURCE_POWER
    return LOAD_SOURCE_PROVIDER


def _recompute_chain(bind, rows: list[sa.engine.Row], loads: dict[str, float | None]):
    """Replay CTL/ATL/TSB per athlete over the loads as they now stand.

    Mirrors ``build_ride_metrics_chain``: rides in date order, ``gap_days`` from
    the previous activity's date, a missing load entering as 0.0.
    """
    ctl = 0.0
    atl = 0.0
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

        load = loads.get(row.id)
        ctl, atl = apply_ctl_atl_decay(ctl, atl, load or 0.0, gap_days=gap_days)
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


def upgrade() -> None:
    if not _table_exists("ride_metrics"):
        return

    if not _column_exists("ride_metrics", "tss_source"):
        op.add_column(
            "ride_metrics", sa.Column("tss_source", sa.String(length=20), nullable=True)
        )

    bind = op.get_bind()
    hr_by_user: dict[str, tuple[int | None, int | None]] = {}
    if _table_exists("users"):
        for user in bind.execute(_SELECT_USERS):
            hr_by_user[user.id] = (user.max_heart_rate, user.resting_heart_rate)

    rows = list(bind.execute(_SELECT_METRICS))
    loads: dict[str, float | None] = {}

    for row in rows:
        if row.tss is not None:
            source = row.tss_source or _existing_load_source(row)
            loads[row.id] = float(row.tss)
            if row.tss_source != source:
                bind.execute(
                    _UPDATE_LOAD,
                    {"row_id": row.id, "tss": float(row.tss), "tss_source": source},
                )
            continue

        max_hr, resting_hr = hr_by_user.get(row.user_id, (None, None))
        load = resolve_training_load(
            duration_seconds=row.duration_seconds,
            sport_type=row.sport_type,
            avg_hr_bpm=_avg_hr(row.perf_signals),
            max_heart_rate=max_hr,
            resting_heart_rate=resting_hr,
        )
        loads[row.id] = load.tss if load is not None else None
        bind.execute(
            _UPDATE_LOAD,
            {
                "row_id": row.id,
                "tss": load.tss if load is not None else None,
                "tss_source": load.source if load is not None else None,
            },
        )

    _recompute_chain(bind, rows, loads)


def downgrade() -> None:
    if not _table_exists("ride_metrics"):
        return
    if not _column_exists("ride_metrics", "tss_source"):
        return

    bind = op.get_bind()
    rows = list(bind.execute(_SELECT_METRICS))
    loads: dict[str, float | None] = {}

    # Drop the loads this migration invented and keep the measured ones, then
    # replay the chain — which reproduces the CTL/ATL/TSB the app computed
    # before, rather than leaving history half-derived under a dropped column.
    for row in rows:
        derived = (
            row.tss_source is not None and row.tss_source not in MEASURED_LOAD_SOURCES
        )
        if row.tss is not None and derived:
            loads[row.id] = None
            bind.execute(
                _UPDATE_LOAD, {"row_id": row.id, "tss": None, "tss_source": None}
            )
        else:
            loads[row.id] = float(row.tss) if row.tss is not None else None

    _recompute_chain(bind, rows, loads)
    op.drop_column("ride_metrics", "tss_source")
