"""import jobs for every source, kept across a restart

ai-trainer-ops#284. The intervals.icu history import kept its progress only in
memory, so a restart in the middle of one answered "nothing running" and the
athlete never learned the import had stopped. ``strava_import_jobs`` was made
for this and nothing has written it for months; it becomes ``import_jobs`` with
the source of each import. Rows already there were Strava's.

Revision ID: 20261025_000001
Revises: 20261023_000001
Create Date: 2026-10-25 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "20261025_000001"
down_revision = "20261023_000001"
branch_labels = None
depends_on = None

OLD = "strava_import_jobs"
NEW = "import_jobs"


def _tables() -> set[str]:
    return set(sa_inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa_inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa_inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()
    if NEW not in tables:
        if OLD in tables:
            op.rename_table(OLD, NEW)
        else:
            op.create_table(
                NEW,
                sa.Column("id", sa.String(length=36), primary_key=True),
                sa.Column(
                    "user_id",
                    sa.String(length=36),
                    sa.ForeignKey("users.id"),
                    nullable=False,
                ),
                sa.Column(
                    "status",
                    sa.String(length=20),
                    nullable=False,
                    server_default="running",
                ),
                sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
                sa.Column(
                    "processed", sa.Integer(), nullable=False, server_default="0"
                ),
                sa.Column("imported", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
                sa.Column(
                    "failed_activities", sa.JSON(), nullable=False, server_default="[]"
                ),
                sa.Column("error", sa.Text(), nullable=False, server_default=""),
                sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            )
    if "source" not in _columns(NEW):
        op.add_column(
            NEW,
            sa.Column(
                "source", sa.String(length=20), nullable=False, server_default="strava"
            ),
        )
        # Every writer names its source; the default was only for the old rows.
        with op.batch_alter_table(NEW) as batch:
            batch.alter_column("source", server_default=None)
    indexes = _indexes(NEW)
    if f"ix_{OLD}_user_id" in indexes:
        op.drop_index(f"ix_{OLD}_user_id", table_name=NEW)
    if f"ix_{NEW}_user_id" not in indexes:
        op.create_index(f"ix_{NEW}_user_id", NEW, ["user_id"])


def downgrade() -> None:
    tables = _tables()
    if NEW not in tables or OLD in tables:
        return
    indexes = _indexes(NEW)
    if f"ix_{NEW}_user_id" in indexes:
        op.drop_index(f"ix_{NEW}_user_id", table_name=NEW)
    if "source" in _columns(NEW):
        # Strava's own rows go back; the others never belonged in that table.
        jobs = sa.table(NEW, sa.column("source", sa.String))
        op.execute(jobs.delete().where(jobs.c.source != "strava"))
        with op.batch_alter_table(NEW) as batch:
            batch.drop_column("source")
    op.rename_table(NEW, OLD)
    op.create_index(f"ix_{OLD}_user_id", OLD, ["user_id"])
