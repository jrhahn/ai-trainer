"""the app's own notes under a coach reply, beside its text

ai-trainer-ops#262. The router and the chat service wrote their notes ("Hinweis:
…") into the reply's text, which was stored and replayed to the model as the
coach's own words. They are stored beside it now, as ``[{"kind", "text"}]``.
Rows written before keep their notes in ``content``.

Revision ID: 20261026_000001
Revises: 20261025_000001
Create Date: 2026-10-26 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261026_000001"
down_revision = "20261025_000001"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("chat_messages")}


def upgrade() -> None:
    if "notes_json" not in _columns():
        op.add_column("chat_messages", sa.Column("notes_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "notes_json" in _columns():
        op.drop_column("chat_messages", "notes_json")
