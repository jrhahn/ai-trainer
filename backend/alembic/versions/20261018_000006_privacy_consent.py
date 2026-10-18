"""when an athlete agreed to the privacy policy at sign-up

ai-trainer-ops#2. Health data need explicit consent (Art. 9(2)(a) GDPR), and
the consent has to be shown (Art. 7(1)), so its time is kept on the account.
Accounts from before have none.

Revision ID: 20261018_000006
Revises: 20261018_000005
Create Date: 2026-10-18 00:00:06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261018_000006"
down_revision = "20261018_000005"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "privacy_consent_at" not in _columns():
        op.add_column(
            "users", sa.Column("privacy_consent_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    if "privacy_consent_at" in _columns():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("privacy_consent_at")
