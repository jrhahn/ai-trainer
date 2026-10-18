"""email addresses are one mailbox regardless of case

ai-trainer-ops#109: EmailStr lowercased only the domain, so Foo@Example.de and
foo@example.de were two accounts and sign-in with another spelling failed.
Normalise existing rows to lowercase and add a unique index on lower(email), so
a case variant can never make a second account even under a concurrent register
that the application-level check would miss.

Aborts before changing anything if two rows already differ only in case: the
index would refuse them, so they must be merged first. **Check production for
such addresses before deploying** (ai-trainer-ops#136).

Revision ID: 20261018_000002
Revises: 20261018_000001
Create Date: 2026-10-18 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261018_000002"
down_revision = "20261018_000001"
branch_labels = None
depends_on = None

_INDEX = "ix_users_email_lower"


def upgrade() -> None:
    bind = op.get_bind()

    # Abort before touching data if two rows already differ only in case: the
    # index below would refuse them, so they must be merged first.
    dupes = bind.execute(
        sa.text(
            "SELECT lower(email) AS e FROM users "
            "GROUP BY lower(email) HAVING count(*) > 1"
        )
    ).fetchall()
    if dupes:
        raise RuntimeError(
            "Cannot add the case-insensitive email index: these addresses "
            "already differ only in case and must be merged first: "
            + ", ".join(row.e for row in dupes)
        )

    # Idempotent: the UPDATE's WHERE is empty on a second run, and the index
    # uses IF NOT EXISTS. Raw DDL so the functional index renders the same on
    # SQLite (the test database) and Postgres, which get_indexes reflection does
    # not do reliably for an expression index.
    bind.execute(
        sa.text("UPDATE users SET email = lower(email) WHERE email <> lower(email)")
    )
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} ON users (lower(email))")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
