"""encrypt strava tokens – widen access/refresh columns to TEXT

Fernet ciphertext is longer than 255 characters, so the columns must be
widened from VARCHAR(255) to TEXT before token encryption is activated.
When STRAVA_ENCRYPTION_KEY is not set the EncryptedString TypeDecorator
stores plaintext, so this migration is safe to run even in that case.

Revision ID: 20260423_000001
Revises: 20260421_000001
Create Date: 2026-04-23 00:00:01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260423_000001"
down_revision = "20260421_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "strava_tokens",
        "access_token",
        existing_type=sa.String(255),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        "strava_tokens",
        "refresh_token",
        existing_type=sa.String(255),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "strava_tokens",
        "refresh_token",
        existing_type=sa.Text(),
        type_=sa.String(255),
        existing_nullable=False,
    )
    op.alter_column(
        "strava_tokens",
        "access_token",
        existing_type=sa.Text(),
        type_=sa.String(255),
        existing_nullable=False,
    )
