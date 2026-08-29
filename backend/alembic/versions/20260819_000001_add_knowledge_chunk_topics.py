"""add knowledge_chunks.topics for limiter-aware retrieval

Revision ID: 20260819_000001
Revises: 20260818_000001
Create Date: 2026-08-29 00:00:01

Carries the ``chunk --[addresses]--> limiter`` relation that pure vector search
cannot express (#627). Tags are written by
``scripts/ingest_cycling_science.py`` via ``services.knowledge_topics``.

The column is nullable and stays NULL for every existing row. That is the
migration's whole safety story: an untagged chunk is ranked exactly as it is
today, so this can land before the corpus is re-ingested and retrieval does not
change until it is.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql


revision = "20260819_000001"
down_revision = "20260818_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "knowledge_chunks" not in set(inspector.get_table_names()):
        return
    if "topics" in {c["name"] for c in inspector.get_columns("knowledge_chunks")}:
        return

    if bind.dialect.name == "postgresql":
        op.add_column(
            "knowledge_chunks",
            sa.Column("topics", postgresql.ARRAY(sa.Text()), nullable=True),
        )
        # GIN is the index for array containment/overlap; retrieval ranks by
        # `topics && :focus`, which cannot use a btree.
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_topics "
            "ON knowledge_chunks USING gin (topics)"
        )
    else:
        # SQLite fallback, matching how the table stores embeddings there: the
        # column exists so the schema lines up, but nothing queries it.
        op.add_column("knowledge_chunks", sa.Column("topics", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "knowledge_chunks" not in set(inspector.get_table_names()):
        return
    if "topics" not in {c["name"] for c in inspector.get_columns("knowledge_chunks")}:
        return

    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_topics")
    op.drop_column("knowledge_chunks", "topics")
