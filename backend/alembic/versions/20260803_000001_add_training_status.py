"""add coach-authored training status to the rider assessment

The dashboard's training-status chip ("On track" / "Slightly behind") was
computed in the browser and never shown to the coach, so the coach could not
explain the label it was being asked about and invented reasons instead (#499).
These columns hold the coach-written label, its tone (which drives the chip
colour) and the rationale behind it, so chip and conversation share one source.

All three are nullable: an existing row simply has no status yet and gets one
lazily on the next dashboard load, exactly like ``login_summary`` before it.

Revision ID: 20260803_000001
Revises: 20260802_000001
Create Date: 2026-08-03 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260803_000001"
down_revision = "20260802_000001"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("training_status_label", sa.String(length=60)),
    ("training_status_tone", sa.String(length=20)),
    ("training_status_rationale", sa.Text()),
)


def _columns(inspector, table: str) -> set[str]:
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    existing = _columns(inspector, "rider_assessments")

    for name, column_type in _COLUMNS:
        if name not in existing:
            op.add_column(
                "rider_assessments", sa.Column(name, column_type, nullable=True)
            )


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    existing = _columns(inspector, "rider_assessments")

    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("rider_assessments", name)
