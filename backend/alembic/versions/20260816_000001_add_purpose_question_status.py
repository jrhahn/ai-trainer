"""give the "what was this session?" question somewhere to live

The coach asked it as prose inside the login summary: no answer field, no state,
no consequence. Making it answerable needs one piece of state per session —
whether the athlete has answered it, waved it away, or not yet seen it (#580).

NULL is the right value for every existing row: the question is open on exactly
the sessions that are still unclassified, which is what
``ride_purpose_question.question_is_open`` decides from the classification. No
backfill, because there is nothing yet to remember about any of them.

Revision ID: 20260816_000001
Revises: 20260815_000001
Create Date: 2026-08-08 00:00:02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260816_000001"
down_revision = "20260815_000001"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    if _column_exists("ride_metrics", "purpose_question_status"):
        return
    op.add_column(
        "ride_metrics",
        sa.Column("purpose_question_status", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    if not _column_exists("ride_metrics", "purpose_question_status"):
        return
    op.drop_column("ride_metrics", "purpose_question_status")
