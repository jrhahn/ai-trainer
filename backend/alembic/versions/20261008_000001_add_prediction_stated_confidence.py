"""keep the confidence a prediction was made with

Calibration asks whether the coach's 0.6 comes true 60% of the time
(ai-trainer-ops#27). ``athlete_predictions.confidence`` cannot answer it:
evaluation nudges it by ±0.2 on the outcome, so a hit is stored higher and a
miss lower, and binning the stored value against the outcome measures the nudge
rather than the coach. ``stated_confidence`` is written once, when the
prediction is made, and never again.

Nullable with no backfill. Reversing the nudge looks exact but is not: the step
is clamped at 0 and 1, and the athlete can edit ``confidence`` through
``PATCH /users/me/predictions/{id}``, and a stored row does not say whether
either happened. NULL reads correctly as "made before this was recorded";
``scripts/calibration_report.py`` can show a separately labelled reconstruction
for those rows, but never mixes it into the measured numbers.

Revision ID: 20261008_000001
Revises: 20261006_000001
Create Date: 2026-10-08 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20261008_000001"
down_revision = "20261006_000001"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa_inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "stated_confidence" in _columns("athlete_predictions"):
        return
    op.add_column(
        "athlete_predictions",
        sa.Column("stated_confidence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    if "stated_confidence" not in _columns("athlete_predictions"):
        return
    op.drop_column("athlete_predictions", "stated_confidence")
