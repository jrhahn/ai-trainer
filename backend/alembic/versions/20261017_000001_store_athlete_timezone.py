"""the athlete's own time zone, as the browser last reported it

ai-trainer-ops#128: the nightly plan job counted every athlete's day in the
app's zone, Europe/Berlin. At 02:00 there it is 20:00 in New York, so that
evening's session counted as missed. ``NULL`` until the app is opened again;
the app's zone stands in until then.

Revision ID: 20261017_000001
Revises: 20261015_000003
Create Date: 2026-10-17 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261017_000001"
down_revision = "20261015_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}
    if "timezone_name" in columns:
        return
    op.add_column("users", sa.Column("timezone_name", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "timezone_name")
