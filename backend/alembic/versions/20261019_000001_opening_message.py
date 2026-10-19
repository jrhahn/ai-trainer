"""when the coach's first chat message was written

ai-trainer-ops#153. The coach speaks first once onboarding is done, exactly
once. Accounts onboarded before this are marked as done, so an athlete who has
used the app for months does not get a welcome out of the blue.

Revision ID: 20261019_000001
Revises: 20261018_000006
Create Date: 2026-10-19 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261019_000001"
down_revision = "20261018_000006"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "opening_message_at" not in _columns():
        op.add_column(
            "users", sa.Column("opening_message_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute(
        sa.text(
            "UPDATE users SET opening_message_at = CURRENT_TIMESTAMP "
            "WHERE is_onboarded AND opening_message_at IS NULL"
        )
    )


def downgrade() -> None:
    if "opening_message_at" in _columns():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("opening_message_at")
