"""answers to tap, and the hypothesis a question tests

ai-trainer-ops#154. A new athlete's first questions come with answer choices,
each saying whether it confirms or refutes the starting hypothesis it tests.
Existing inquiries have neither.

Revision ID: 20261019_000002
Revises: 20261019_000001
Create Date: 2026-10-19 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261019_000002"
down_revision = "20261019_000001"
branch_labels = None
depends_on = None

_INDEX = "ix_athlete_inquiries_hypothesis_id"


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("athlete_inquiries")}


def _indexes() -> set[str]:
    return {i["name"] for i in sa_inspect(op.get_bind()).get_indexes("athlete_inquiries")}


def upgrade() -> None:
    columns = _columns()
    if "options" not in columns:
        op.add_column("athlete_inquiries", sa.Column("options", sa.JSON(), nullable=True))
    if "hypothesis_id" not in columns:
        op.add_column(
            "athlete_inquiries", sa.Column("hypothesis_id", sa.String(36), nullable=True)
        )
    if _INDEX not in _indexes():
        op.create_index(_INDEX, "athlete_inquiries", ["hypothesis_id"])


def downgrade() -> None:
    if _INDEX in _indexes():
        op.drop_index(_INDEX, table_name="athlete_inquiries")
    columns = _columns()
    with op.batch_alter_table("athlete_inquiries") as batch:
        for name in ("hypothesis_id", "options"):
            if name in columns:
                batch.drop_column(name)
