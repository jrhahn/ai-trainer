"""add kind to athlete_memory_facts (#386: separate facts from observations)

Revision ID: 20260712_000001
Revises: 20260710_000001
Create Date: 2026-07-12 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260712_000001"
down_revision = "20260710_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("athlete_memory_facts")}
    if "kind" not in existing_columns:
        # Existing rows were all produced by insight generation — inferred
        # behavioural patterns — so they backfill as observations, not facts.
        op.add_column(
            "athlete_memory_facts",
            sa.Column(
                "kind",
                sa.String(length=20),
                nullable=False,
                server_default="observation",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("athlete_memory_facts")}
    if "kind" in existing_columns:
        op.drop_column("athlete_memory_facts", "kind")
