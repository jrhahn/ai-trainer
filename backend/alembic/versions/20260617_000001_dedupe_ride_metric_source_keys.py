"""dedupe ride metric source activity keys

Revision ID: 20260617_000001
Revises: 20260611_000001
Create Date: 2026-06-17 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect as sa_inspect


revision = "20260617_000001"
down_revision = "20260611_000001"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _dedupe_source_external_rows() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        user_id,
                        COALESCE(activity_source, 'strava'),
                        COALESCE(external_activity_id, CAST(strava_activity_id AS VARCHAR))
                    ORDER BY
                        created_at DESC,
                        activity_date DESC,
                        COALESCE(activity_start_datetime, '') DESC,
                        id DESC
                ) AS dedupe_rank
            FROM ride_metrics
        )
        DELETE FROM ride_metrics
        WHERE id IN (
            SELECT id FROM ranked WHERE dedupe_rank > 1
        )
        """
    )


def upgrade() -> None:
    columns = _column_names("ride_metrics")
    if not {
        "activity_source",
        "external_activity_id",
        "strava_activity_id",
    }.issubset(columns):
        return

    op.execute(
        "UPDATE ride_metrics "
        "SET activity_source = 'strava' "
        "WHERE activity_source IS NULL"
    )
    op.execute(
        "UPDATE ride_metrics "
        "SET external_activity_id = CAST(strava_activity_id AS VARCHAR) "
        "WHERE external_activity_id IS NULL"
    )
    _dedupe_source_external_rows()

    if "ix_ride_metrics_user_source_external" not in _index_names("ride_metrics"):
        op.create_index(
            "ix_ride_metrics_user_source_external",
            "ride_metrics",
            ["user_id", "activity_source", "external_activity_id"],
            unique=True,
        )


def downgrade() -> None:
    # The source/external unique index is owned by 20260611_000001.  This
    # follow-up only repairs data and recreates the index if an earlier upgrade
    # could not, so downgrading should preserve the 20260611 schema.
    pass
