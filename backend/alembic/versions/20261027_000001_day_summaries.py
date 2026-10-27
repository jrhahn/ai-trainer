"""the coach's one sentence per changed day, under its reply

ai-trainer-ops#266. A chat reply that changes the plan carries, beside its text,
one sentence per day it changed, in the athlete's language. The app shows them
under the reply, so they are stored beside it as ``[{"date", "summary"}]``, for
the days the plan took. Not in ``content``: that is the coach's own words.

Revision ID: 20261027_000001
Revises: 20261026_000002
Create Date: 2026-10-27 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261027_000001"
down_revision = "20261026_000002"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("chat_messages")}


def upgrade() -> None:
    if "day_summaries_json" not in _columns():
        op.add_column(
            "chat_messages",
            sa.Column("day_summaries_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if "day_summaries_json" in _columns():
        op.drop_column("chat_messages", "day_summaries_json")
