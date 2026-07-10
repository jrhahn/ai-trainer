"""add long-term athlete model

Revision ID: 20260710_000001
Revises: 20260709_000004
Create Date: 2026-07-10 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260710_000001"
down_revision = "20260709_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "athlete_model" in inspector.get_table_names():
        return

    op.create_table(
        "athlete_model",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("ftp_watts", sa.Integer(), nullable=True),
        sa.Column("vo2max", sa.Float(), nullable=True),
        sa.Column("pacing_quality", sa.Text(), nullable=False, server_default=""),
        sa.Column("recovery_ability", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "threshold_durability", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("heat_tolerance", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "preferred_training_style", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("weaknesses", sa.JSON(), nullable=False),
        sa.Column("risk_factors", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
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
    if "athlete_model" not in inspector.get_table_names():
        return
    op.drop_table("athlete_model")
