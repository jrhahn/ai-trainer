"""add athlete_home_location table (persisted, editable training location)

Revision ID: 20260801_000001
Revises: 20260731_000001
Create Date: 2026-08-01 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260801_000001"
down_revision = "20260731_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    if "athlete_home_location" not in inspector.get_table_names():
        op.create_table(
            "athlete_home_location",
            sa.Column("user_id", sa.String(36), primary_key=True),
            sa.Column("latitude", sa.Float(), nullable=False),
            sa.Column("longitude", sa.Float(), nullable=False),
            sa.Column(
                "label", sa.String(120), nullable=False, server_default=""
            ),
            sa.Column(
                "source", sa.String(20), nullable=False, server_default="inferred"
            ),
            sa.Column(
                "confidence", sa.Float(), nullable=False, server_default="0"
            ),
            sa.Column(
                "ride_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)

    if "athlete_home_location" in inspector.get_table_names():
        op.drop_table("athlete_home_location")
