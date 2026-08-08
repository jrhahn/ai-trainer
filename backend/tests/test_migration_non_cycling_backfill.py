"""The #578 backfill: rows stored as unclassifiable rides that were never rides.

Structural migration checks live in ``test_migrations.py``; this one runs the
data migration itself against a real (SQLite) ``ride_metrics`` table, because
the risk here is not a broken revision graph but an UPDATE that touches the
wrong rows.
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
    / "20260814_000001_reclassify_non_cycling_activities.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_backfill_578", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(engine, activity_id: int) -> sa.engine.Row:
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT ride_purpose, classification_confidence, "
                "classification_reason, summary FROM ride_metrics "
                "WHERE strava_activity_id = :id"
            ),
            {"id": activity_id},
        ).one()


@pytest.fixture()
def engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path/'backfill.db'}")
    models.RideMetric.__table__.create(engine)

    rows = [
        # The prod case: a gym session labelled an unclassifiable ride.
        (1, "WeightTraining", "unknown", "low", "Insufficient stream data to classify ride reliably."),
        # A hike whose classification was never written at all.
        (2, "Hike", None, None, None),
        # A bike ride we genuinely could not classify — must stay untouched.
        (3, "MountainBikeRide", "unknown", "low", "Insufficient stream data to classify ride reliably."),
        # A classified ride — the backfill must never overwrite a real answer.
        (4, "Ride", "interval_vo2max", "high", "Repeated efforts at VO2max power."),
    ]
    # Insert through the table so every column default the model declares is
    # applied — the migration reads real rows, not a hand-built subset.
    insert = models.RideMetric.__table__.insert()
    with engine.begin() as conn:
        for activity_id, sport_type, purpose, confidence, reason in rows:
            conn.execute(
                insert,
                {
                    "user_id": 1,
                    "strava_activity_id": activity_id,
                    "activity_source": "intervals",
                    "activity_date": "2026-08-06",
                    "sport_type": sport_type,
                    "duration_seconds": 3483,
                    "ride_purpose": purpose,
                    "classification_confidence": confidence,
                    "classification_reason": reason,
                    "summary": "Unknown ride · 58m",
                },
            )
    return engine


def _run(engine, direction: str) -> None:
    migration = _load_migration()
    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            getattr(migration, direction)()


def test_backfill_names_the_sport_of_a_non_cycling_row(engine):
    _run(engine, "upgrade")

    strength = _row(engine, 1)
    assert strength.ride_purpose == "strength"
    assert strength.classification_confidence == "high"
    assert "Insufficient stream data" not in strength.classification_reason
    assert strength.summary.startswith("Strength")

    hike = _row(engine, 2)
    assert hike.ride_purpose == "hike"
    assert hike.classification_confidence == "high"


def test_backfill_leaves_rides_alone(engine):
    _run(engine, "upgrade")

    unclassified_ride = _row(engine, 3)
    assert unclassified_ride.ride_purpose == "unknown"
    assert unclassified_ride.classification_confidence == "low"

    classified_ride = _row(engine, 4)
    assert classified_ride.ride_purpose == "interval_vo2max"
    assert classified_ride.classification_reason == "Repeated efforts at VO2max power."


def test_backfill_is_idempotent(engine):
    _run(engine, "upgrade")
    first = _row(engine, 1)
    _run(engine, "upgrade")
    assert _row(engine, 1) == first


def test_downgrade_only_reverts_what_this_migration_wrote(engine):
    _run(engine, "upgrade")
    _run(engine, "downgrade")

    reverted = _row(engine, 1)
    assert reverted.ride_purpose == "unknown"
    assert reverted.classification_confidence == "low"

    untouched = _row(engine, 4)
    assert untouched.ride_purpose == "interval_vo2max"
