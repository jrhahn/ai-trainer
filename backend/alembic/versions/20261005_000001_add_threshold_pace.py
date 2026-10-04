"""give the athlete a running threshold, the way they already have a cycling one

Running load has reached CTL from heart rate or from an assumed cost per hour
(#712). Both are inferences about a session; neither needs to know how fast the
athlete is, which is why running has had no threshold column while cycling has
had ``current_ftp`` since the first schema.

rTSS needs one. ``services.run_model`` prices a run as hours x IF^2 x 100 with IF
taken on grade-adjusted pace against this value, so without it the pace rung
cannot answer and the run stays on heart rate — which is the correct behaviour,
and exactly why the column has to be nullable rather than defaulted. A default
threshold pace would price every athlete's runs against a stranger's.

Seconds per kilometre, as a float. Seconds because "4:20" is a string and
nothing can compute with it; per kilometre because that is the unit a runner
knows their own threshold in. The Critical Speed fit over the athlete's run
history fills the gap when they have not stated one and the fit is confident
enough (``metrics_service.get_effective_threshold_pace``) — it never writes
here, so an inference can never be mistaken later for something the athlete
said.

Revision ID: 20261005_000001
Revises: 20261004_000001
Create Date: 2026-10-05 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20261005_000001"
down_revision = "20261004_000001"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa_inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "threshold_pace_seconds_per_km" in _columns("users"):
        return
    op.add_column(
        "users",
        sa.Column("threshold_pace_seconds_per_km", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    if "threshold_pace_seconds_per_km" not in _columns("users"):
        return
    op.drop_column("users", "threshold_pace_seconds_per_km")
