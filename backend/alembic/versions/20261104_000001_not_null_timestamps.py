"""six timestamps NOT NULL, as the models have said all along

Review of 2026-11-01, ai-trainer-ops#338 C12. The first run of the Postgres
schema check found six columns the models declare NOT NULL that their
migrations created nullable. Every writer goes through the models, whose
defaults fill them, so a NULL is not expected; one that is there anyway gets
the nearest honest value before the constraint is set: the row's own creation
time where it has one, else the time of this migration.

Revision ID: 20261104_000001
Revises: 20261101_000002
Create Date: 2026-11-04 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261104_000001"
down_revision = "20261101_000002"
branch_labels = None
depends_on = None

# (table, column, what a NULL becomes)
_COLUMNS = (
    ("plan_change_summary", "recorded_at", "CURRENT_TIMESTAMP"),
    ("race_events", "created_at", "CURRENT_TIMESTAMP"),
    ("race_events", "updated_at", "COALESCE(created_at, CURRENT_TIMESTAMP)"),
    ("strength_sets", "created_at", "CURRENT_TIMESTAMP"),
    ("totp_recovery_codes", "created_at", "CURRENT_TIMESTAMP"),
    ("trusted_devices", "created_at", "CURRENT_TIMESTAMP"),
)


def upgrade() -> None:
    for table, column, fallback in _COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = {fallback} WHERE {column} IS NULL")
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, existing_type=sa.DateTime(timezone=True), nullable=False
            )


def downgrade() -> None:
    for table, column, _ in reversed(_COLUMNS):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column, existing_type=sa.DateTime(timezone=True), nullable=True
            )
