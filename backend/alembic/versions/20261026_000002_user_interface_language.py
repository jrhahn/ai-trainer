"""the interface language an athlete reads the app in

ai-trainer-ops#271. A plan the night rewrites has new texts, and the page asked
for their German only when the athlete opened it, so a German tester met them in
English first. The app reports its interface language with every request; it
is kept on the account, as the time zone is, so the night can write the German
too.

Revision ID: 20261026_000002
Revises: 20261026_000001
Create Date: 2026-10-26 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261026_000002"
down_revision = "20261026_000001"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "interface_language" not in _columns():
        op.add_column(
            "users", sa.Column("interface_language", sa.String(16), nullable=True)
        )


def downgrade() -> None:
    if "interface_language" in _columns():
        op.drop_column("users", "interface_language")
