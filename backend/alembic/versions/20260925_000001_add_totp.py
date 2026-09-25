"""add TOTP second factor, trusted devices and recovery codes

Revision ID: 20260925_000001
Revises: 20260821_000001
Create Date: 2026-09-25 00:00:01

Three additions for #688:

- ``users.totp_secret`` / ``totp_enabled`` / ``totp_confirmed_at``. The secret
  is written through ``EncryptedString``, so the column is plain text here and
  the ciphertext is produced by the application — the same arrangement the
  provider-key columns already use.
- ``trusted_devices``, so a device can skip the second factor for a while and
  the athlete can take that back.
- ``totp_recovery_codes``, hashed and single-use.

Additive only, and nothing switches on until a user enrolls: ``totp_enabled``
defaults to false, and every code path checks it. So this is safe to deploy
ahead of the routes that use it, and safe to leave in place if those are
rolled back.

Idempotent in the same way as the rest of this directory — the schema is also
reachable via ``create_all`` in development, so a migration that assumed a
clean slate would fail there.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "20260925_000001"
down_revision = "20260821_000001"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "users" in tables:
        if not _has_column(inspector, "users", "totp_secret"):
            op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))
        if not _has_column(inspector, "users", "totp_enabled"):
            op.add_column(
                "users",
                sa.Column(
                    "totp_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )
        if not _has_column(inspector, "users", "totp_confirmed_at"):
            op.add_column(
                "users",
                sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True),
            )

    if "trusted_devices" not in tables:
        op.create_table(
            "trusted_devices",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False, index=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("user_agent", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "totp_recovery_codes" not in tables:
        op.create_table(
            "totp_recovery_codes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("code_hash", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())

    if "totp_recovery_codes" in tables:
        op.drop_table("totp_recovery_codes")
    if "trusted_devices" in tables:
        op.drop_table("trusted_devices")

    if "users" in tables:
        for column in ("totp_confirmed_at", "totp_enabled", "totp_secret"):
            if _has_column(inspector, "users", column):
                op.drop_column("users", column)
