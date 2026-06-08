"""add intervals tokens

Revision ID: 20260607_000001
Revises: 20260530_000001
Create Date: 2026-06-07 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260607_000001"
down_revision = "20260530_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "intervals_tokens" not in inspector.get_table_names():
        op.create_table(
            "intervals_tokens",
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("api_key", sa.Text(), nullable=False),
            sa.Column("athlete_id", sa.String(64), nullable=False, server_default="0"),
            sa.Column("athlete_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "intervals_tokens" in inspector.get_table_names():
        op.drop_table("intervals_tokens")
