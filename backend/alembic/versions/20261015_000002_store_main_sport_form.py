"""store the athlete's form, not the logged session's sport's

#713 stored ``tsb_after`` as the logged session's own sport's CTL minus the
aggregate ATL. After a gym hour that is a strength CTL built from a few sessions
set against all the fatigue, about −50 for anyone who mostly rides, and a login
summary read it out as the athlete's form: "TSB −52.4" (2026-10-15).

``tsb_after`` is now the main sport's form (``LoadLedger.form``). Only the stored
TSB changes, and it is a function of values each row already holds — the ledger
and the ATL — so this recomputes it from them instead of replaying the chain.
Snapshots are rewritten the same way. A row or snapshot without a ledger is
pre-#713 and cycling-only, where both readings are the same number.

Idempotent. Like 20261003_000001 it imports the live ledger.

Revision ID: 20261015_000002
Revises: 20261015_000001
Create Date: 2026-10-15 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op
from domain.fitness_ledger import ledger_from_row, ledger_sport

revision = "20261015_000002"
down_revision = "20261015_000001"
branch_labels = None
depends_on = None

# (table, SELECT, UPDATE, digits its writer rounds TSB to). The snapshot never
# stored a sport, and the old reading needed none: its own CTL minus its ATL.
_TABLES = (
    (
        "ride_metrics",
        sa.text(
            "SELECT id, ctl_after AS ctl, atl_after AS atl, ctl_by_sport, "
            "sport_type AS sport FROM ride_metrics "
            "WHERE ctl_by_sport IS NOT NULL AND atl_after IS NOT NULL"
        ).columns(ctl_by_sport=sa.JSON()),
        sa.text("UPDATE ride_metrics SET tsb_after = :tsb WHERE id = :row_id"),
        2,
    ),
    (
        "athlete_metric_snapshots",
        sa.text(
            "SELECT id, ctl, atl, ctl_by_sport, NULL AS sport "
            "FROM athlete_metric_snapshots "
            "WHERE ctl_by_sport IS NOT NULL AND atl IS NOT NULL"
        ).columns(ctl_by_sport=sa.JSON()),
        sa.text("UPDATE athlete_metric_snapshots SET tsb = :tsb WHERE id = :row_id"),
        1,
    ),
)


def _rewrite(*, main_sport: bool) -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())
    for table, select, update, digits in _TABLES:
        if table not in tables:
            continue
        for row in bind.execute(select).all():
            if not row.ctl_by_sport:  # a JSON null: no ledger either
                continue
            ledger = ledger_from_row(
                row.ctl_by_sport, ctl_after=row.ctl, atl_after=row.atl
            )
            if main_sport:
                tsb = ledger.form()
            elif row.sport is not None:
                tsb = ledger.tsb(ledger_sport(row.sport))
            else:
                tsb = float(row.ctl or 0.0) - ledger.atl
            bind.execute(update, {"tsb": round(tsb, digits), "row_id": row.id})


def upgrade() -> None:
    _rewrite(main_sport=True)


def downgrade() -> None:
    _rewrite(main_sport=False)
