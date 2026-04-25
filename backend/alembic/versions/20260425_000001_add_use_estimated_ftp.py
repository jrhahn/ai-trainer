"""add use_estimated_ftp to users

Revision ID: 20260425_000001
Revises: 20260423_000001
Create Date: 2026-04-25 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260425_000001"
down_revision = "20260423_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "use_estimated_ftp" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "use_estimated_ftp",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "use_estimated_ftp" in columns:
        op.drop_column("users", "use_estimated_ftp")
