"""consents to the weekly coach review

ai-trainer-ops#136, phase 2 of #143. Each consent is a row; a withdrawal sets
withdrawn_at and keeps the row, so the consent stays demonstrable (Art. 7(1)
GDPR).

Revision ID: 20261020_000002
Revises: 20261020_000001
Create Date: 2026-10-20 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261020_000002"
down_revision = "20261020_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "coach_review_consents" in sa_inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "coach_review_consents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("given_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_coach_review_consents_user_id", "coach_review_consents", ["user_id"]
    )


def downgrade() -> None:
    if "coach_review_consents" in sa_inspect(op.get_bind()).get_table_names():
        op.drop_index("ix_coach_review_consents_user_id", "coach_review_consents")
        op.drop_table("coach_review_consents")
