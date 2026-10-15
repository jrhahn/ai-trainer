"""20261015_000002: stored TSB becomes the athlete's form, not the row sport's.

Runs the data migration on real ``ride_metrics`` and ``athlete_metric_snapshots``
tables. The prod case is a rider whose newest row is a gym hour: its stored TSB
was the strength rung minus all the fatigue.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

import models

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20261015_000002_store_main_sport_form.py"
)
USER_ID = "user-1"
LEDGER = {"cycling": 35.0, "strength": 0.9}


def _run(engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_main_sport_form", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


def _tsb(engine, table: str, column: str, row_id: int) -> float:
    with engine.connect() as conn:
        return conn.execute(
            sa.text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": row_id}
        ).scalar_one()


@pytest.fixture()
def engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'form.db'}")
    for model in (models.User, models.RideMetric, models.AthleteMetricSnapshot):
        model.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            models.User.__table__.insert(),
            {"id": USER_ID, "email": "athlete@example.com", "hashed_password": "x"},
        )
        rides = models.RideMetric.__table__.insert()
        common = {"user_id": USER_ID, "activity_source": "strava", "duration_seconds": 2760}
        # The gym hour, as #713 stored it: 0.9 − 65.2.
        conn.execute(rides, {**common, "id": 1, "strava_activity_id": 1,
                             "external_activity_id": "1", "activity_date": "2026-10-14",
                             "sport_type": "WeightTraining", "ctl_after": 0.9,
                             "atl_after": 65.2, "tsb_after": -64.3, "ctl_by_sport": LEDGER})
        # Pre-#713: no ledger, cycling-only, already the same number either way.
        conn.execute(rides, {**common, "id": 2, "strava_activity_id": 2,
                             "external_activity_id": "2", "activity_date": "2026-09-01",
                             "sport_type": "Ride", "ctl_after": 40.0,
                             "atl_after": 50.0, "tsb_after": -10.0})
        conn.execute(
            models.AthleteMetricSnapshot.__table__.insert(),
            {"id": 1, "user_id": USER_ID, "ctl": 0.9, "atl": 65.2, "tsb": -64.3,
             "ctl_by_sport": LEDGER, "source": "manual_recalculate"},
        )
    return engine


def test_the_gym_row_stores_the_cycling_form(engine):
    _run(engine, "upgrade")

    assert _tsb(engine, "ride_metrics", "tsb_after", 1) == pytest.approx(-30.2)
    assert _tsb(engine, "athlete_metric_snapshots", "tsb", 1) == pytest.approx(-30.2)
    assert _tsb(engine, "ride_metrics", "tsb_after", 2) == pytest.approx(-10.0)


def test_upgrade_is_idempotent_and_downgrade_restores_the_old_reading(engine):
    _run(engine, "upgrade")
    _run(engine, "upgrade")
    assert _tsb(engine, "ride_metrics", "tsb_after", 1) == pytest.approx(-30.2)

    _run(engine, "downgrade")
    assert _tsb(engine, "ride_metrics", "tsb_after", 1) == pytest.approx(-64.3)
    assert _tsb(engine, "athlete_metric_snapshots", "tsb", 1) == pytest.approx(-64.3)
    assert _tsb(engine, "ride_metrics", "tsb_after", 2) == pytest.approx(-10.0)
