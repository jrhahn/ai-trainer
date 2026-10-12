"""short-lived athlete states said in chat: ill, or a day with less to give

ai-trainer-ops#18, #39 Q1. "I'm ill" reached the planner only as chat text.
These rows hold the kind and the dates, never the sentence, and the planner
reads them as priors.

Revision ID: 20261012_000002
Revises: 20261012_000001
Create Date: 2026-10-12 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261012_000002"
down_revision = "20261012_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "athlete_states" in sa_inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "athlete_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("starts_on", sa.String(10), nullable=False),
        sa.Column("ends_on", sa.String(10), nullable=True),
        sa.Column("said_on", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_athlete_states_user_id", "athlete_states", ["user_id"])


def downgrade() -> None:
    if "athlete_states" in sa_inspect(op.get_bind()).get_table_names():
        op.drop_index("ix_athlete_states_user_id", table_name="athlete_states")
        op.drop_table("athlete_states")
