"""add structured athlete context

Revision ID: 20260618_000001
Revises: 20260617_000001
Create Date: 2026-06-18 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260618_000001"
down_revision = "20260617_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "athlete_context" in inspector.get_table_names():
        return

    op.create_table(
        "athlete_context",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "training_tendency",
            sa.String(length=30),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "rest_response",
            sa.String(length=30),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("motivation_drivers", sa.JSON(), nullable=False),
        sa.Column(
            "adherence_pattern",
            sa.String(length=30),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("weaknesses", sa.JSON(), nullable=False),
        sa.Column("preferred_terrain", sa.JSON(), nullable=False),
        sa.Column("preferred_session_types", sa.JSON(), nullable=False),
        sa.Column("coaching_risks", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "athlete_context" in inspector.get_table_names():
        op.drop_table("athlete_context")
