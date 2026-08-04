"""add input/output/cached token counters to users

Revision ID: 20260808_000001
Revises: 20260807_000001
Create Date: 2026-08-04 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260808_000001"
down_revision = "20260807_000001"
branch_labels = None
depends_on = None

_COLUMNS = (
    "consumed_input_tokens",
    "consumed_output_tokens",
    "consumed_cached_tokens",
)


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _column_names("users")
    for name in _COLUMNS:
        if name in existing:
            continue
        # Backfilled to 0 rather than to a share of the existing
        # consumed_tokens: the split was never recorded, and inventing one
        # would make historical rows look priceable when they are not.
        op.add_column(
            "users",
            sa.Column(
                name, sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
        )


def downgrade() -> None:
    existing = _column_names("users")
    for name in _COLUMNS:
        if name in existing:
            op.drop_column("users", name)
