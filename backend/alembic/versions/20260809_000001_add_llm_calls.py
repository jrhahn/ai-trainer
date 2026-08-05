"""add llm_calls table for durable per-call cost records

Revision ID: 20260809_000001
Revises: 20260808_000001
Create Date: 2026-08-05 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260809_000001"
down_revision = "20260808_000001"
branch_labels = None
depends_on = None

_TABLE = "llm_calls"


def _has_table(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        # Nullable: a call made outside every collection scope belongs to
        # nobody, and those are the ones worth finding (#537).
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=60), nullable=False),
        sa.Column(
            "input_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "output_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "cached_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "latency_ms", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "json_mode", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("prompt_sha", sa.String(length=12), nullable=False),
    )
    # No backfill is possible: the records this table exists to keep were in
    # container logs that are already gone.
    op.create_index("ix_llm_calls_created_at", _TABLE, ["created_at"])
    op.create_index(
        "ix_llm_calls_source_created_at", _TABLE, ["source", "created_at"]
    )


def downgrade() -> None:
    if not _has_table(_TABLE):
        return
    op.drop_index("ix_llm_calls_source_created_at", table_name=_TABLE)
    op.drop_index("ix_llm_calls_created_at", table_name=_TABLE)
    op.drop_table(_TABLE)
