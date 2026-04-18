"""add knowledge_chunks table for cycling science RAG

Revision ID: 20260418_000001
Revises: 20260414_000001
Create Date: 2026-04-18 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql


revision = "20260418_000001"
down_revision = "20260414_000001"
branch_labels = None
depends_on = None

_EMBEDDING_DIM = 1536  # text-embedding-3-small output dimension


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # pgvector extension and vector column only make sense on PostgreSQL.
    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    inspector = sa_inspect(bind)
    existing = set(inspector.get_table_names())

    if "knowledge_chunks" not in existing:
        if dialect == "postgresql":
            # Use the native vector column type for similarity search.
            op.create_table(
                "knowledge_chunks",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("source_id", sa.String(length=512), nullable=False),
                sa.Column("chunk_index", sa.Integer(), nullable=False),
                sa.Column("title", sa.Text(), nullable=False),
                sa.Column("content", sa.Text(), nullable=False),
                sa.Column("source_type", sa.String(length=50), nullable=False),
                sa.Column("doi", sa.String(length=255), nullable=True),
                sa.Column("url", sa.Text(), nullable=True),
                sa.Column(
                    "embedding",
                    postgresql.ARRAY(sa.Float()),
                    nullable=True,
                ),
                sa.Column(
                    "created_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.func.now(),
                    nullable=False,
                ),
                sa.UniqueConstraint("source_id", "chunk_index", name="uq_knowledge_chunk"),
            )
            # Convert the ARRAY column to vector type so pgvector operators work.
            op.execute(
                f"ALTER TABLE knowledge_chunks "
                f"ALTER COLUMN embedding TYPE vector({_EMBEDDING_DIM}) "
                f"USING embedding::vector({_EMBEDDING_DIM})"
            )
            # HNSW index for fast approximate nearest-neighbour search.
            op.execute(
                "CREATE INDEX ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        else:
            # Fallback for non-PostgreSQL environments (e.g. SQLite in tests):
            # store embedding as JSON text so the table can still be created.
            op.create_table(
                "knowledge_chunks",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("source_id", sa.String(length=512), nullable=False),
                sa.Column("chunk_index", sa.Integer(), nullable=False),
                sa.Column("title", sa.Text(), nullable=False),
                sa.Column("content", sa.Text(), nullable=False),
                sa.Column("source_type", sa.String(length=50), nullable=False),
                sa.Column("doi", sa.String(length=255), nullable=True),
                sa.Column("url", sa.Text(), nullable=True),
                sa.Column("embedding", sa.Text(), nullable=True),
                sa.Column(
                    "created_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.func.now(),
                    nullable=False,
                ),
                sa.UniqueConstraint("source_id", "chunk_index", name="uq_knowledge_chunk"),
            )


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
