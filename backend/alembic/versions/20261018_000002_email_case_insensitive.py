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
Revises: 20261017_000001
Create Date: 2026-10-18 00:00:02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261018_000002"
# NB: re-parent to 20261018_000001 once ai-trainer-ops#133 (#944) is on develop.
down_revision = "20261017_000001"
branch_labels = None
depends_on = None

_INDEX = "ix_users_email_lower"


def upgrade() -> None:
    bind = op.get_bind()
    if _INDEX in {ix["name"] for ix in sa_inspect(bind).get_indexes("users")}:
        return

    # Abort before touching data if two rows already differ only in case.
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

    bind.execute(
        sa.text("UPDATE users SET email = lower(email) WHERE email <> lower(email)")
    )
    op.create_index(_INDEX, "users", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="users")
