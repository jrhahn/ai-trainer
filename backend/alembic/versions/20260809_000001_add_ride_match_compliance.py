"""add match_score/match_label to ride_metrics

Persists the compliance badge that used to be computed only in the browser, so
the coach can see the badge it is asked to explain (#551).

Revision ID: 20260809_000001
Revises: 20260808_000001
Create Date: 2026-08-09 00:00:01
"""

import json
from types import SimpleNamespace

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from services import plan_compliance


revision = "20260809_000001"
down_revision = "20260808_000001"
branch_labels = None
depends_on = None


def _as_dict(value: object) -> dict | None:
    """The snapshot column reads back as a dict on Postgres, a str on SQLite."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _backfill(bind) -> None:
    """Score every already-matched ride against the plan day it matched to.

    Without this, existing rides stay unbadged in the coach's context and the
    dashboard would keep falling back to its local computation for them.
    """
    rows = bind.execute(
        sa.text(
            "SELECT id, duration_seconds, normalized_power_w, avg_power_w, "
            "intensity_factor, tss, matched_plan_snapshot FROM ride_metrics "
            "WHERE matched_plan_snapshot IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        snapshot = _as_dict(row.matched_plan_snapshot)
        if snapshot is None:
            continue
        ride = SimpleNamespace(
            duration_seconds=row.duration_seconds,
            normalized_power_w=row.normalized_power_w,
            avg_power_w=row.avg_power_w,
            intensity_factor=row.intensity_factor,
            tss=row.tss,
        )
        score, label = plan_compliance.score_and_label(ride, snapshot)
        if score is None and label is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE ride_metrics SET match_score = :score, match_label = :label "
                "WHERE id = :id"
            ),
            {"score": score, "label": label, "id": row.id},
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "match_score" not in existing_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("match_score", sa.Integer(), nullable=True),
        )
    if "match_label" not in existing_columns:
        op.add_column(
            "ride_metrics",
            sa.Column("match_label", sa.String(50), nullable=True),
        )
    _backfill(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}
    if "match_label" in existing_columns:
        op.drop_column("ride_metrics", "match_label")
    if "match_score" in existing_columns:
        op.drop_column("ride_metrics", "match_score")
