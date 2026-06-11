"""add activity source metadata to ride metrics

Revision ID: 20260611_000001
Revises: 20260608_000002
Create Date: 2026-06-11 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260611_000001"
down_revision = "20260608_000002"
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


def upgrade() -> None:
    columns = _column_names("ride_metrics")
    if not columns:
        return

    if "activity_source" not in columns:
        op.add_column(
            "ride_metrics",
            sa.Column(
                "activity_source",
                sa.String(length=50),
                nullable=False,
                server_default="strava",
            ),
        )
    if "external_activity_id" not in columns:
        op.add_column(
            "ride_metrics",
            sa.Column("external_activity_id", sa.String(length=255), nullable=True),
        )
    if "source_metadata" not in columns:
        op.add_column(
            "ride_metrics",
            sa.Column("source_metadata", sa.JSON(), nullable=True),
        )

    op.execute(
        "UPDATE ride_metrics "
        "SET activity_source = 'strava', external_activity_id = CAST(strava_activity_id AS VARCHAR) "
        "WHERE external_activity_id IS NULL"
    )

    if "ix_ride_metrics_user_source_external" not in _index_names("ride_metrics"):
        op.create_index(
            "ix_ride_metrics_user_source_external",
            "ride_metrics",
            ["user_id", "activity_source", "external_activity_id"],
            unique=True,
        )


def downgrade() -> None:
    indexes = _index_names("ride_metrics")
    columns = _column_names("ride_metrics")
    if "ix_ride_metrics_user_source_external" in indexes:
        op.drop_index("ix_ride_metrics_user_source_external", table_name="ride_metrics")
    if "source_metadata" in columns:
        op.drop_column("ride_metrics", "source_metadata")
    if "external_activity_id" in columns:
        op.drop_column("ride_metrics", "external_activity_id")
    if "activity_source" in columns:
        op.drop_column("ride_metrics", "activity_source")
