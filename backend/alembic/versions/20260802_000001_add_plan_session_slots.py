"""add session slots for multiple sessions per day (two-a-days)

Adds ``plan_day_history.slot``, ``workout_logs.slot`` and
``ride_metrics.matched_plan_slot`` so a plan day can hold more than one session,
each session can carry its own feedback, and each executed activity can attach to
its own planned session (#496).

Existing rows are all single-session days: history and workout-log rows backfill
to slot 0 (the server default), and ``matched_plan_slot`` stays NULL, which every
reader treats as slot 0. So no stored plan, log or match changes meaning.

Revision ID: 20260802_000001
Revises: 20260801_000001
Create Date: 2026-08-02 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260802_000001"
down_revision = "20260801_000001"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa_inspect(op.get_bind())

    if "slot" not in _columns(inspector, "plan_day_history"):
        op.add_column(
            "plan_day_history",
            sa.Column(
                "slot", sa.Integer(), nullable=False, server_default="0"
            ),
        )

    if "slot" not in _columns(inspector, "workout_logs"):
        op.add_column(
            "workout_logs",
            sa.Column(
                "slot", sa.Integer(), nullable=False, server_default="0"
            ),
        )

    if "matched_plan_slot" not in _columns(inspector, "ride_metrics"):
        op.add_column(
            "ride_metrics",
            sa.Column("matched_plan_slot", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())

    if "matched_plan_slot" in _columns(inspector, "ride_metrics"):
        op.drop_column("ride_metrics", "matched_plan_slot")

    if "slot" in _columns(inspector, "workout_logs"):
        op.drop_column("workout_logs", "slot")

    if "slot" in _columns(inspector, "plan_day_history"):
        op.drop_column("plan_day_history", "slot")
