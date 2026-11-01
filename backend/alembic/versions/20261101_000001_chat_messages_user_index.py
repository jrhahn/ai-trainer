"""an index on chat_messages (user_id, timestamp)

Every chat read filters on ``user_id`` and orders by ``timestamp``; the table
had no index beside its primary key, so each read scanned every athlete's
messages (review of 2026-11-01, ai-trainer-ops#338 C16).

Revision ID: 20261101_000001
Revises: 20261027_000001
Create Date: 2026-11-01 00:00:01
"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261101_000001"
down_revision = "20261027_000001"
branch_labels = None
depends_on = None

INDEX = "ix_chat_messages_user_timestamp"


def _indexes() -> set[str]:
    return {i["name"] for i in sa_inspect(op.get_bind()).get_indexes("chat_messages")}


def upgrade() -> None:
    if INDEX not in _indexes():
        op.create_index(INDEX, "chat_messages", ["user_id", "timestamp"])


def downgrade() -> None:
    if INDEX in _indexes():
        op.drop_index(INDEX, table_name="chat_messages")
