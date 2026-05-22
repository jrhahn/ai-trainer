"""add per-user consumed token counter

Revision ID: 20260522_000001
Revises: 20260511_000001
Create Date: 2026-05-22 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260522_000001"
down_revision = "20260511_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("users")}

    if "consumed_tokens" not in existing_columns:
        op.add_column(
            "users",
            sa.Column(
                "consumed_tokens",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("users")}

    if "consumed_tokens" in existing_columns:
        op.drop_column("users", "consumed_tokens")
