"""add users.token_generation so issued JWTs can be revoked

Revision ID: 20261001_000001
Revises: 20260925_000001
Create Date: 2026-10-01 00:00:01

One counter per user (#704). Every access token carries the value this column
had when it was minted, and ``get_current_user`` refuses a token whose claim
disagrees with the row — so incrementing it ends every session for that
account.

Existing rows get 0, and a token issued before this change carries no claim at
all, which the application reads as 0. The two therefore agree, and deploying
this signs nobody out. The first revocation is what starts rejecting tokens.

Idempotent like the rest of this directory: the schema is also reachable via
``create_all`` in development, so a migration that assumed the column was
absent would fail there.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "20261001_000001"
down_revision = "20260925_000001"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    if "users" not in set(inspector.get_table_names()):
        return
    if _has_column(inspector, "users", "token_generation"):
        return

    # ``server_default`` rather than a backfill UPDATE: it fills existing rows
    # and keeps the column non-null for an INSERT issued by an older process
    # during a rolling deploy.
    op.add_column(
        "users",
        sa.Column(
            "token_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    if "users" not in set(inspector.get_table_names()):
        return
    if _has_column(inspector, "users", "token_generation"):
        op.drop_column("users", "token_generation")
