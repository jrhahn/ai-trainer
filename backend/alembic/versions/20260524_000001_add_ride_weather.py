"""add ride weather fields

Revision ID: 20260524_000001
Revises: 20260522_000002
Create Date: 2026-05-24 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "20260524_000001"
down_revision = "20260522_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}

    columns = [
        ("start_lat", sa.Column("start_lat", sa.Float(), nullable=True)),
        ("start_lng", sa.Column("start_lng", sa.Float(), nullable=True)),
        ("weather_temperature_c", sa.Column("weather_temperature_c", sa.Float(), nullable=True)),
        (
            "weather_apparent_temperature_c",
            sa.Column("weather_apparent_temperature_c", sa.Float(), nullable=True),
        ),
        ("weather_condition", sa.Column("weather_condition", sa.String(50), nullable=True)),
        ("weather_code", sa.Column("weather_code", sa.Integer(), nullable=True)),
        ("weather_wind_speed_kph", sa.Column("weather_wind_speed_kph", sa.Float(), nullable=True)),
        (
            "weather_precipitation_mm",
            sa.Column("weather_precipitation_mm", sa.Float(), nullable=True),
        ),
        ("weather_source", sa.Column("weather_source", sa.String(50), nullable=True)),
    ]
    for name, column in columns:
        if name not in existing_columns:
            op.add_column("ride_metrics", column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("ride_metrics")}

    for column in (
        "weather_source",
        "weather_precipitation_mm",
        "weather_wind_speed_kph",
        "weather_code",
        "weather_condition",
        "weather_apparent_temperature_c",
        "weather_temperature_c",
        "start_lng",
        "start_lat",
    ):
        if column in existing_columns:
            op.drop_column("ride_metrics", column)
