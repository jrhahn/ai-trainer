"""add user last_login

Revision ID: 20260522_000002
Revises: 20260522_000001
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "20260522_000002"
down_revision = "20260522_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_login")
