"""add threshold_heart_rate to users

Revision ID: 20260415_000001
Revises: 20260414_000001
Create Date: 2026-04-15 00:00:01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260415_000001"
down_revision = "20260414_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("threshold_heart_rate", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "threshold_heart_rate")
