"""password reset links that were sent and not yet used

ai-trainer-ops#35. Only the SHA-256 of each token is stored.

Revision ID: 20261015_000001
Revises: 20261014_000002
Create Date: 2026-10-15 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261015_000001"
down_revision = "20261014_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "password_reset_tokens" in sa_inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"]
    )


def downgrade() -> None:
    if "password_reset_tokens" in sa_inspect(op.get_bind()).get_table_names():
        op.drop_index(
            "ix_password_reset_tokens_user_id", table_name="password_reset_tokens"
        )
        op.drop_table("password_reset_tokens")
