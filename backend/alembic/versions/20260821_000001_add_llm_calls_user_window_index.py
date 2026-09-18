"""index llm_calls by (user_id, created_at) for the per-user token budget

Revision ID: 20260821_000001
Revises: 20260820_000001
Create Date: 2026-09-18 00:00:01

The AI token budget (#676) asks "what has this user spent in the last N days"
before every AI request. ``llm_calls`` already indexes ``created_at`` and
``(source, created_at)`` — neither helps a query narrowed to one user, so that
question would scan the table on the hot path of every coach message.

Index only: no column or table changes, so the deploy is safe to run ahead of
the code that issues the query, and safe to leave in place if it is rolled back.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect as sa_inspect


revision = "20260821_000001"
down_revision = "20260820_000001"
branch_labels = None
depends_on = None

_INDEX = "ix_llm_calls_user_id_created_at"
_TABLE = "llm_calls"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    existing = {index["name"] for index in inspector.get_indexes(_TABLE)}
    if _INDEX in existing:
        return
    op.create_index(_INDEX, _TABLE, ["user_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    existing = {index["name"] for index in inspector.get_indexes(_TABLE)}
    if _INDEX not in existing:
        return
    op.drop_index(_INDEX, table_name=_TABLE)
