"""the offers under a coach reply, stored with it

Review of 2026-11-01, ai-trainer-ops#338 (T1). A reply can carry offers with
buttons: a race to add or move, a race to remove, a profile change. They lived
only in the browser, so after a reload the coach's text pointed at a button
that was gone. They are stored beside the reply now, as
``{"raceOffer", "raceProposal", "profileProposal", "resolved"}``, where
``resolved`` maps an offer the athlete answered to "accepted" or "dismissed".

Revision ID: 20261101_000002
Revises: 20261101_000001
Create Date: 2026-11-01 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261101_000002"
down_revision = "20261101_000001"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns("chat_messages")}


def upgrade() -> None:
    if "offers_json" not in _columns():
        op.add_column(
            "chat_messages",
            sa.Column("offers_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if "offers_json" in _columns():
        op.drop_column("chat_messages", "offers_json")
