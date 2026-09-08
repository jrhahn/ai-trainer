"""add plan_commitments — coaching arrangements that span days pins do not protect

Revision ID: 20260820_000001
Revises: 20260819_000001
Create Date: 2026-09-08 00:00:01

A pin protects the day the coach wrote; it does not protect the day that day was
written *for*. This table carries the arrangement itself, so the pipeline gate
can stop an automated write from contradicting something the athlete agreed to
(#667).

New table only — nothing existing changes shape, so the deploy is safe to run
ahead of the code that reads it: with no rows, every writer behaves exactly as
it does today.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260820_000001"
down_revision = "20260819_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "plan_commitments" in set(inspector.get_table_names()):
        return

    op.create_table(
        "plan_commitments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("start_date", sa.String(length=10), nullable=False),
        sa.Column("end_date", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=30),
            nullable=False,
            server_default="coach_chat",
        ),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Every read is "the active commitments for this athlete", and expiry sweeps
    # by end_date.
    op.create_index(
        "ix_plan_commitments_user_active", "plan_commitments", ["user_id", "active"]
    )
    op.create_index(
        "ix_plan_commitments_user_end_date",
        "plan_commitments",
        ["user_id", "end_date"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "plan_commitments" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_plan_commitments_user_end_date", table_name="plan_commitments")
    op.drop_index("ix_plan_commitments_user_active", table_name="plan_commitments")
    op.drop_table("plan_commitments")
