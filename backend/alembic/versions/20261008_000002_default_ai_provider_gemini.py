"""move accounts that never chose OpenAI to Gemini

``users.ai_provider`` defaulted to ``"openai"``, although the deployment runs
Gemini and every athlete-facing text says so (ai-trainer-ops#42). An account
with ``openai`` stored and no OpenAI key of its own got there by default, not
by choice — the setting is only meaningful with a key behind it — so it moves
to ``gemini``. An account that saved an OpenAI key keeps OpenAI: that was a
choice.

``resolve_user_provider`` already routes such an account to Gemini whenever
Gemini is usable, so nothing changes for a request that works today. What
changes is the one that does not: with no usable key, the error now names the
provider the athlete was told to get a key for.

The column's server-side default stays as the model's Python default; there is
no ``server_default`` to change.

Revision ID: 20261008_000002
Revises: 20261008_000001
Create Date: 2026-10-08 00:00:02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20261008_000002"
down_revision = "20261008_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE users SET ai_provider = 'gemini' "
            "WHERE ai_provider = 'openai' AND user_openai_api_key IS NULL"
        )
    )


def downgrade() -> None:
    # Not reversible: after the upgrade, "gemini by default" and "gemini by
    # choice" are the same row. Leaving them is the honest downgrade.
    pass
