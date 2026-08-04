"""add athlete open questions

Revision ID: 20260711_000001
Revises: 20260710_000001
Create Date: 2026-07-11 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260711_000001"
down_revision = "20260710_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "athlete_open_questions" in inspector.get_table_names():
        return

    op.create_table(
        "athlete_open_questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_key", sa.String(length=255), nullable=False),
        sa.Column(
            "category", sa.String(length=50), nullable=False, server_default="general"
        ),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("needs", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "evidence_count", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="open"
        ),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column(
            "first_asked_at",
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
        "ix_athlete_open_questions_user_id",
        "athlete_open_questions",
        ["user_id"],
    )
    op.create_index(
        "ix_athlete_open_questions_user_category_key",
        "athlete_open_questions",
        ["user_id", "category", "question_key"],
        unique=True,
    )
    op.create_index(
        "ix_athlete_open_questions_user_status",
        "athlete_open_questions",
        ["user_id", "status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "athlete_open_questions" not in inspector.get_table_names():
        return
    op.drop_index(
        "ix_athlete_open_questions_user_status",
        table_name="athlete_open_questions",
    )
    op.drop_index(
        "ix_athlete_open_questions_user_category_key",
        table_name="athlete_open_questions",
    )
    op.drop_index(
        "ix_athlete_open_questions_user_id",
        table_name="athlete_open_questions",
    )
    op.drop_table("athlete_open_questions")
