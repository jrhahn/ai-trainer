"""add plan_day_history

Revision ID: 20260701_000001
Revises: 20260628_000001
Create Date: 2026-07-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260701_000001"
down_revision = "20260628_000001"
branch_labels = None
depends_on = None


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "plan_day_history" in inspector.get_table_names():
        return

    op.create_table(
        "plan_day_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("old_day", sa.JSON(), nullable=True),
        sa.Column("new_day", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column(
            "applied", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_plan_day_history_user_id",
        "plan_day_history",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_plan_day_history_user_date",
        "plan_day_history",
        ["user_id", "date"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "plan_day_history" not in inspector.get_table_names():
        return

    indexes = _index_names("plan_day_history")
    for index_name in (
        "ix_plan_day_history_user_date",
        "ix_plan_day_history_user_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="plan_day_history")
    op.drop_table("plan_day_history")
