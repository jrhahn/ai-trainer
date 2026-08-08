"""give the coach's uncertainty records an end, and record it when they reach it

Three of four uncertainty channels only grew: 74 hypotheses proposed and none
resolved, 23 open questions still open, 20 experiments never run. The inflow was
~18 hypotheses a week and the outflow was zero (#581).

This adds the audit table the lifecycle writes to. A record that leaves the set
should leave a trace — the same reasoning as the weight events in #566 — and
``evidence_count`` on an expiry row is also the metric that says whether the
falsifiability rules worked, since 96 % of hypotheses died at one observation.

No backfill of the existing rows: they are stale by any window this policy
defines, and the first learning pass after deploy expires them through the
normal gate, writing the audit line that a bulk UPDATE here would not.

Revision ID: 20260817_000001
Revises: 20260816_000001
Create Date: 2026-08-08 00:00:03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260817_000001"
down_revision = "20260816_000001"
branch_labels = None
depends_on = None

_TABLE = "athlete_uncertainty_events"


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
        # uncertainty_lifecycle.CHANNEL_* / EVENT_*.
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("event", sa.String(length=30), nullable=False),
        # Null for a candidate declined at the ceiling: it never got a row.
        sa.Column("record_id", sa.String(length=36), nullable=True),
        # Denormalised so the trace outlives the record it describes.
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        # How much support the record had when it left. This is what makes
        # "did falsifiability improve?" a query rather than an opinion.
        sa.Column("evidence_count", sa.Integer(), nullable=True),
        sa.Column("age_days", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(f"ix_{_TABLE}_user_id", _TABLE, ["user_id"])
    op.create_index(f"ix_{_TABLE}_recorded_at", _TABLE, ["recorded_at"])
    op.create_index(
        f"ix_{_TABLE}_user_channel", _TABLE, ["user_id", "channel", "recorded_at"]
    )


def downgrade() -> None:
    if not _has_table(_TABLE):
        return
    op.drop_index(f"ix_{_TABLE}_user_channel", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_recorded_at", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
