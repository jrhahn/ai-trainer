"""add validation experiments

Revision ID: 20260709_000003
Revises: 20260709_000002
Create Date: 2026-07-09 00:00:03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260709_000003"
down_revision = "20260709_000002"
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
    if "validation_experiments" in inspector.get_table_names():
        return

    op.create_table(
        "validation_experiments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("hypothesis_id", sa.String(length=36), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("protocol", sa.Text(), nullable=False),
        sa.Column("protocol_key", sa.String(length=255), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=False,
            server_default="general",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="suggested",
        ),
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
        "ix_validation_experiments_user_id",
        "validation_experiments",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_validation_experiments_user_key",
        "validation_experiments",
        ["user_id", "protocol_key"],
        unique=True,
    )
    op.create_index(
        "ix_validation_experiments_user_status",
        "validation_experiments",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "validation_experiments" not in inspector.get_table_names():
        return

    indexes = _index_names("validation_experiments")
    for index_name in (
        "ix_validation_experiments_user_status",
        "ix_validation_experiments_user_key",
        "ix_validation_experiments_user_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="validation_experiments")
    op.drop_table("validation_experiments")
