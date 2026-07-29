"""add evidence + alternative_explanations columns to athlete hypotheses

Revision ID: 20260731_000001
Revises: 20260730_000001
Create Date: 2026-07-31 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260731_000001"
down_revision = "20260730_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("athlete_hypotheses")}
    if "evidence" not in cols:
        op.add_column(
            "athlete_hypotheses",
            sa.Column("evidence", sa.JSON(), nullable=True),
        )
    if "alternative_explanations" not in cols:
        op.add_column(
            "athlete_hypotheses",
            sa.Column("alternative_explanations", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("athlete_hypotheses")}
    if "alternative_explanations" in cols:
        op.drop_column("athlete_hypotheses", "alternative_explanations")
    if "evidence" in cols:
        op.drop_column("athlete_hypotheses", "evidence")
