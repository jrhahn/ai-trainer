"""add memory_updates_enabled to users

Revision ID: 20260622_000001
Revises: 20260618_000003
Create Date: 2026-06-22 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260622_000001"
down_revision = "20260618_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "memory_updates_enabled" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "memory_updates_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "memory_updates_enabled" in columns:
        op.drop_column("users", "memory_updates_enabled")
