"""keep the distance the athlete entered for a session

An activity entered by hand (ai-trainer-ops#47) could say how long it took but
not how far it went, and the field the issue asks for has nowhere to live:
``workout_logs`` has duration, effort and a note, and nothing about distance.

Nullable, with no backfill and no default. NULL is "they did not say", which is
a different statement from 0 km and has to stay distinguishable: a logged
strength session has no distance at all, and a run the athlete left blank must
not read as a run of zero kilometres — that is the #579 confusion (absence
treated as a measured zero) in another column.

Kilometres rather than metres, because kilometres are what the athlete types and
what ``race_events.distance_km`` already stores. ``RideMetric`` needs no column:
distance there lives in ``perf_signals["distance_m"]``, which is where
``run_durability.activity_run_distance_km`` reads weekly mileage from.

Revision ID: 20261010_000001
Revises: 20261008_000002
Create Date: 2026-10-10 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20261010_000001"
down_revision = "20261008_000002"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa_inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "distance_km" in _columns("workout_logs"):
        return
    op.add_column("workout_logs", sa.Column("distance_km", sa.Float(), nullable=True))


def downgrade() -> None:
    if "distance_km" not in _columns("workout_logs"):
        return
    op.drop_column("workout_logs", "distance_km")
