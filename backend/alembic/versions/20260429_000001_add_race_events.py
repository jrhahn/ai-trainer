"""add race events

Revision ID: 20260429_000001
Revises: 20260428_000001
Create Date: 2026-04-29 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260429_000001"
down_revision = "20260428_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "race_events" in inspector.get_table_names():
        return

    op.create_table(
        "race_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.String(length=20), nullable=False),
        sa.Column("start_time", sa.String(length=10), nullable=True),
        sa.Column("distance_km", sa.Float(), nullable=False),
        sa.Column("elevation_m", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_race_events_user_id", "race_events", ["user_id"])
    op.create_index("ix_race_events_date", "race_events", ["date"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "race_events" not in inspector.get_table_names():
        return

    op.drop_index("ix_race_events_date", table_name="race_events")
    op.drop_index("ix_race_events_user_id", table_name="race_events")
    op.drop_table("race_events")
