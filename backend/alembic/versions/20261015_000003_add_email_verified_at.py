"""when an account confirmed its email address

app #687. ``NULL`` means not confirmed. Every account that exists already counts
as confirmed: it was made before there was anything to confirm with.

Revision ID: 20261015_000003
Revises: 20261015_000002
Create Date: 2026-10-15 00:00:03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261015_000003"
down_revision = "20261015_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}
    if "email_verified_at" in columns:
        return
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE users SET email_verified_at = COALESCE(created_at, CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_column("users", "email_verified_at")
