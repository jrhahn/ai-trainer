"""a session's average heart rate, for scoring heart-rate intervals

ai-trainer-ops#133: a plan can set its intervals in heart rate since #117, but
the compliance score read only watts, and the average heart rate was computed
for the load and thrown away. ``NULL`` for sessions imported before this, and
for any without a heart-rate stream or provider average; those are scored on
duration, as before.

Revision ID: 20261018_000001
Revises: 20261017_000001
Create Date: 2026-10-18 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261018_000001"
down_revision = "20261017_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa_inspect(op.get_bind()).get_columns("ride_metrics")}
    if "avg_heart_rate_bpm" in columns:
        return
    op.add_column(
        "ride_metrics", sa.Column("avg_heart_rate_bpm", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ride_metrics", "avg_heart_rate_bpm")
