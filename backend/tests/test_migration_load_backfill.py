"""The #579 backfill: history in which every non-power session was a rest day.

Structural migration checks live in ``test_migrations.py``; this one runs the
data migration against real ``users`` and ``ride_metrics`` tables, because the
risk is not a broken revision graph but a chain recomputed from the wrong loads.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

import models

BACKEND_DIR = Path(__file__).parent.parent
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "20260815_000001_add_tss_source_and_backfill_load.py"
)

USER_ID = "user-1"


def _load_migration():
    spec = importlib.util.spec_from_file_location("_backfill_579", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(engine, activity_id: int) -> sa.engine.Row:
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT sport_type, tss, tss_source, ctl_after, atl_after, tsb_after "
                "FROM ride_metrics WHERE strava_activity_id = :id"
            ),
            {"id": activity_id},
        ).one()


@pytest.fixture()
def engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path/'load.db'}")
    models.User.__table__.create(engine)
    models.RideMetric.__table__.create(engine)

    # The column does not exist yet at the point the migration runs; the model
    # declares it, so drop it and let the migration add it back.
    with engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE ride_metrics DROP COLUMN tss_source"))

    with engine.begin() as conn:
        conn.execute(
            models.User.__table__.insert(),
            {
                "id": USER_ID,
                "email": "athlete@example.com",
                "hashed_password": "x",
                "max_heart_rate": 185,
                "resting_heart_rate": 50,
            },
        )

    rows = [
        # A ride with a power-derived load: labelled, never recomputed.
        (1, "2026-08-04", "MountainBikeRide", 4560, 220, 94.0, None),
        # The prod case: a ride the day before the gym session.
        (2, "2026-08-05", "MountainBikeRide", 4560, 210, 88.0, None),
        # An hour of strength training that entered the chain as a rest day.
        (3, "2026-08-06", "WeightTraining", 3483, None, None, {"avg_hr_bpm": 118}),
        # A hike with no HR either — only time on task is left.
        (4, "2026-08-07", "Hike", 7200, None, None, None),
        # A provider load with no normalized power of our own.
        (5, "2026-08-08", "Ride", 3600, None, 63.0, None),
    ]
    insert = models.RideMetric.__table__.insert()
    with engine.begin() as conn:
        for activity_id, day, sport, seconds, np_w, tss, signals in rows:
            conn.execute(
                insert,
                {
                    "user_id": USER_ID,
                    "strava_activity_id": activity_id,
                    "activity_source": "intervals",
                    "external_activity_id": str(activity_id),
                    "activity_date": day,
                    "sport_type": sport,
                    "duration_seconds": seconds,
                    "normalized_power_w": np_w,
                    "tss": tss,
                    "perf_signals": signals,
                    "ctl_after": 0.0,
                    "atl_after": 0.0,
                    "tsb_after": 0.0,
                },
            )
    return engine


def _run(engine, direction: str) -> None:
    migration = _load_migration()
    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            getattr(migration, direction)()


def test_the_gym_session_stops_being_a_rest_day(engine):
    _run(engine, "upgrade")

    gym = _row(engine, 3)
    assert gym.tss is not None and gym.tss > 0
    # It kept an average heart rate, so the estimate comes off that and not off
    # a flat per-hour assumption.
    assert gym.tss_source == "heart_rate"


def test_training_no_longer_makes_the_athlete_look_fresher(engine):
    """The headline symptom: TSB rose by 5,85 points across the gym session."""
    _run(engine, "upgrade")

    ride_day = _row(engine, 2)
    gym_day = _row(engine, 3)
    assert gym_day.tsb_after <= ride_day.tsb_after


def test_a_session_with_no_heart_rate_still_gets_a_load(engine):
    _run(engine, "upgrade")

    hike = _row(engine, 4)
    assert hike.tss is not None and hike.tss > 0
    assert hike.tss_source == "duration"


def test_measured_loads_are_labelled_and_never_altered(engine):
    _run(engine, "upgrade")

    powered = _row(engine, 1)
    assert powered.tss == 94.0
    assert powered.tss_source == "power"

    provider = _row(engine, 5)
    assert provider.tss == 63.0
    assert provider.tss_source == "provider"


def test_the_backfill_is_idempotent(engine):
    _run(engine, "upgrade")
    first = [_row(engine, i) for i in (1, 2, 3, 4, 5)]
    _run(engine, "upgrade")
    assert [_row(engine, i) for i in (1, 2, 3, 4, 5)] == first


def test_downgrade_restores_the_chain_it_found(engine):
    """Not just the column: leaving history half-derived under a dropped column
    would be worse than either state."""
    _run(engine, "upgrade")
    derived_gym_tsb = _row(engine, 3).tsb_after
    _run(engine, "downgrade")

    with engine.connect() as conn:
        columns = {
            row[1] for row in conn.execute(sa.text("PRAGMA table_info(ride_metrics)"))
        }
    assert "tss_source" not in columns

    with engine.connect() as conn:
        gym = conn.execute(
            sa.text(
                "SELECT tss, ctl_after, atl_after, tsb_after FROM ride_metrics "
                "WHERE strava_activity_id = 3"
            )
        ).one()
        powered = conn.execute(
            sa.text("SELECT tss FROM ride_metrics WHERE strava_activity_id = 1")
        ).one()

    assert gym.tss is None
    assert gym.tsb_after > derived_gym_tsb  # back to the rest-day reading
    assert powered.tss == 94.0
