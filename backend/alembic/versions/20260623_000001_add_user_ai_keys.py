"""add per-user AI provider key columns

Revision ID: 20260623_000001
Revises: 20260622_000001
Create Date: 2026-06-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "20260623_000001"
down_revision = "20260622_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "user_openai_api_key" not in columns:
        op.add_column("users", sa.Column("user_openai_api_key", sa.Text(), nullable=True))
    if "user_gemini_api_key" not in columns:
        op.add_column("users", sa.Column("user_gemini_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "user_openai_api_key" in columns:
        op.drop_column("users", "user_openai_api_key")
    if "user_gemini_api_key" in columns:
        op.drop_column("users", "user_gemini_api_key")
