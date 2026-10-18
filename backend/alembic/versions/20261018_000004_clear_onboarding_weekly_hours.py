"""clear every stored weekly-hours value

ai-trainer-ops#116. Onboarding no longer asks how many hours a week an athlete
has: most do not know, or misjudge it, and an untouched slider was saved as six
hours of their word. The owner chose to clear every stored value (2026-10-18).
The few set from a chat confirmation are cleared too; they are stored in the
same column and cannot be told apart, and the athlete can state them again.

Not reversible: the downgrade cannot bring the values back.

Revision ID: 20261018_000004
Revises: 20261018_000003
Create Date: 2026-10-18 00:00:04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261018_000004"
down_revision = "20261018_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE users SET weekly_hours = NULL WHERE weekly_hours IS NOT NULL"))


def downgrade() -> None:
    # The cleared values are gone; there is nothing to restore.
    pass
