"""add intervals sync cursor

Revision ID: 20260607_000002
Revises: 20260607_000001
Create Date: 2026-06-07 00:00:02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260607_000002"
down_revision = "20260607_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("users")}

    if "intervals_analysis_complete" not in existing_columns:
        op.add_column(
            "users",
            sa.Column(
                "intervals_analysis_complete",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "last_intervals_activity_id" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("last_intervals_activity_id", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("users")}

    if "last_intervals_activity_id" in existing_columns:
        op.drop_column("users", "last_intervals_activity_id")
    if "intervals_analysis_complete" in existing_columns:
        op.drop_column("users", "intervals_analysis_complete")
