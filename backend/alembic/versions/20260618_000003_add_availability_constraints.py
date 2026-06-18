"""add athlete availability constraints

Revision ID: 20260618_000003
Revises: 20260618_000002
Create Date: 2026-06-18 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260618_000003"
down_revision = "20260618_000002"
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
    if "athlete_availability_constraints" in inspector.get_table_names():
        return

    op.create_table(
        "athlete_availability_constraints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "constraint_type",
            sa.String(length=30),
            nullable=False,
            server_default="no_training",
        ),
        sa.Column("constraint_date", sa.String(length=10), nullable=True),
        sa.Column("weekday", sa.String(length=10), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("expires_on", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_athlete_availability_constraints_user_active",
        "athlete_availability_constraints",
        ["user_id", "active"],
        unique=False,
    )
    op.create_index(
        "ix_athlete_availability_constraints_user_date",
        "athlete_availability_constraints",
        ["user_id", "constraint_date"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "athlete_availability_constraints" not in inspector.get_table_names():
        return

    indexes = _index_names("athlete_availability_constraints")
    for index_name in (
        "ix_athlete_availability_constraints_user_date",
        "ix_athlete_availability_constraints_user_active",
    ):
        if index_name in indexes:
            op.drop_index(
                index_name, table_name="athlete_availability_constraints"
            )
    op.drop_table("athlete_availability_constraints")
