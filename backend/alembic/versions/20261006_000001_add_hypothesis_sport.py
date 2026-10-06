"""let a hypothesis say which sport it is about

The deterministic performance-model hypotheses (#479) were written when there was
one sport, so nothing had to say which. #716 and #717 gave running its own
attributes, and #718 gives it its own limiter and its own hypotheses — at which
point "the athlete's limiter is likely threshold utilization" and "the athlete's
sustainable running pace lags behind their top-end speed" are claims about
different sports that live in the same table.

They do not collide: the uniqueness index is (user, category, statement_key) and
the statements differ, so evidence never pools across sports. But the sport was
recoverable only by reading the statement text, and inferring a fact from prose
is what this epic has spent six issues removing.

Nullable with no default, and no backfill. Every existing row is an LLM-formed
hypothesis or a cycling one from before per-sport reasoning existed; writing
"cycling" into them would be asserting something about the LLM's rows that
nobody checked, and NULL already reads correctly as "this row never said".

Revision ID: 20261006_000001
Revises: 20261005_000001
Create Date: 2026-10-06 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20261006_000001"
down_revision = "20261005_000001"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa_inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "sport" in _columns("athlete_hypotheses"):
        return
    op.add_column(
        "athlete_hypotheses",
        sa.Column("sport", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    if "sport" not in _columns("athlete_hypotheses"):
        return
    op.drop_column("athlete_hypotheses", "sport")
