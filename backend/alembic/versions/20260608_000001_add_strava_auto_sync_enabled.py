"""add strava auto sync preference

Revision ID: 20260608_000001
Revises: 20260607_000002
Create Date: 2026-06-08 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260608_000001"
down_revision = "20260607_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("users")}

    if "strava_auto_sync_enabled" not in existing_columns:
        op.add_column(
            "users",
            sa.Column(
                "strava_auto_sync_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("users")}

    if "strava_auto_sync_enabled" in existing_columns:
        op.drop_column("users", "strava_auto_sync_enabled")
