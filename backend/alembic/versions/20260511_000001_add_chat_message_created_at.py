"""add chat message insertion timestamp

Revision ID: 20260511_000001
Revises: 20260506_000001
Create Date: 2026-05-11 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260511_000001"
down_revision = "20260506_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("chat_messages")}

    if "created_at" not in existing_columns:
        op.add_column(
            "chat_messages",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("chat_messages")}

    if "created_at" in existing_columns:
        op.drop_column("chat_messages", "created_at")
