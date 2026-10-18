"""plan texts in the athlete's interface language

ai-trainer-ops#121. Plan days stay English because the matching reads English
words in them; the German an athlete reads is stored here, one row per English
text, looked up by the text's SHA-256.

Revision ID: 20261018_000003
Revises: 20261018_000002
Create Date: 2026-10-18 00:00:03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261018_000003"
down_revision = "20261018_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "plan_text_translations" in sa_inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "plan_text_translations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "language", "source_hash", name="uq_plan_text_translations_text"
        ),
    )


def downgrade() -> None:
    if "plan_text_translations" in sa_inspect(op.get_bind()).get_table_names():
        op.drop_table("plan_text_translations")
