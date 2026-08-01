"""add athlete inquiries — the questions the coach puts to the athlete

Every other uncertainty the coach tracks resolves itself from training data:
open questions accrue evidence, experiments produce it on demand. This table
holds the residual (#506) — the things no future ride will ever reveal, which
the coach has to ask the athlete about directly, pinned in the chat until
answered.

Revision ID: 20260804_000001
Revises: 20260803_000001
Create Date: 2026-08-04 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "20260804_000001"
down_revision = "20260803_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if "athlete_inquiries" in inspector.get_table_names():
        return

    op.create_table(
        "athlete_inquiries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_key", sa.String(length=255), nullable=False),
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=False,
            server_default="general",
        ),
        sa.Column("why_asking", sa.Text(), nullable=False, server_default=""),
        sa.Column("settings_hint", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("ask_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("follow_up_note", sa.Text(), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_athlete_inquiries_user_category_key",
        "athlete_inquiries",
        ["user_id", "category", "question_key"],
        unique=True,
    )
    op.create_index(
        "ix_athlete_inquiries_user_status",
        "athlete_inquiries",
        ["user_id", "status"],
    )


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if "athlete_inquiries" not in inspector.get_table_names():
        return
    op.drop_index("ix_athlete_inquiries_user_status", table_name="athlete_inquiries")
    op.drop_index(
        "ix_athlete_inquiries_user_category_key", table_name="athlete_inquiries"
    )
    op.drop_table("athlete_inquiries")
