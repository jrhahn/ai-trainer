"""add athlete hypotheses

Revision ID: 20260709_000002
Revises: 20260709_000001
Create Date: 2026-07-09 00:00:02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260709_000002"
down_revision = "20260709_000001"
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
    if "athlete_hypotheses" in inspector.get_table_names():
        return

    op.create_table(
        "athlete_hypotheses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("statement_key", sa.String(length=255), nullable=False),
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=False,
            server_default="general",
        ),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default="0.35",
        ),
        sa.Column(
            "evidence_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column(
            "first_proposed_at",
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
        "ix_athlete_hypotheses_user_id",
        "athlete_hypotheses",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_athlete_hypotheses_user_category_key",
        "athlete_hypotheses",
        ["user_id", "category", "statement_key"],
        unique=True,
    )
    op.create_index(
        "ix_athlete_hypotheses_user_status",
        "athlete_hypotheses",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "athlete_hypotheses" not in inspector.get_table_names():
        return

    indexes = _index_names("athlete_hypotheses")
    for index_name in (
        "ix_athlete_hypotheses_user_status",
        "ix_athlete_hypotheses_user_category_key",
        "ix_athlete_hypotheses_user_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="athlete_hypotheses")
    op.drop_table("athlete_hypotheses")
