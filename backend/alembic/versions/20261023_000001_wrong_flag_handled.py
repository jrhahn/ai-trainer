"""when a "this is wrong" flag was answered or turned into an issue

ai-trainer-ops#96. One gate for wave 2 is that every flag a tester sent was
answered or turned into an issue. The admin page marks a flag handled, with the
owner's note on how, and the wave report counts the ones still open.

Revision ID: 20261023_000001
Revises: 20261022_000002
Create Date: 2026-10-23 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261023_000001"
down_revision = "20261022_000002"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("wrong_flags")}


def upgrade() -> None:
    columns = _columns()
    if "handled_at" not in columns:
        op.add_column(
            "wrong_flags", sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "handled_note" not in columns:
        op.add_column("wrong_flags", sa.Column("handled_note", sa.String(200), nullable=True))


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("wrong_flags") as batch:
        if "handled_note" in columns:
            batch.drop_column("handled_note")
        if "handled_at" in columns:
            batch.drop_column("handled_at")
