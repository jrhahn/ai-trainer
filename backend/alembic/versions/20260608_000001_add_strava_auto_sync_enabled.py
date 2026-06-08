"""add activity auto sync preferences

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

    columns = (
        "strava_auto_sync_enabled",
        "intervals_auto_sync_enabled",
    )
    for column in columns:
        if column not in existing_columns:
            op.add_column(
                "users",
                sa.Column(
                    column,
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("users")}

    for column in ("intervals_auto_sync_enabled", "strava_auto_sync_enabled"):
        if column in existing_columns:
            op.drop_column("users", column)
