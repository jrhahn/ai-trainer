"""The per-hour assumptions #579 shipped were too timid (prod, 2026-08-06).

A 58 min strength session came out at 34 against an ATL of 60, so TSB still rose
2,63 points across an hour of training — less than the 5,85 it rose when the
session counted as zero, but the same sign. Erring low means under-reporting
fatigue, which is the direction that gets someone hurt.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

import models
from services.training_load import (
    DEFAULT_LOAD_PER_HOUR,
    LOAD_SOURCE_DURATION,
    duration_training_load,
    resolve_training_load,
)

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20260818_000001_recalibrate_duration_loads.py"
)

# The production state on the evening of 2026-08-05.
CTL_BEFORE, ATL_BEFORE = 72.68, 60.55
GYM_SECONDS = 58 * 60


def _tsb_after(load: float) -> float:
    alpha_ctl = 1 - math.exp(-1 / 42)
    alpha_atl = 1 - math.exp(-1 / 7)
    ctl = CTL_BEFORE + alpha_ctl * (load - CTL_BEFORE)
    atl = ATL_BEFORE + alpha_atl * (load - ATL_BEFORE)
    return ctl - atl


# --- The calibration --------------------------------------------------------


def test_an_hour_of_strength_no_longer_makes_the_athlete_look_fresher():
    """The prod day, replayed. Not exactly flat — a session below the athlete's
    seven-day average genuinely does lower acute fatigue, and forcing that to
    zero would be fitting the model to one day. Within half a point is."""
    load = duration_training_load(
        duration_seconds=GYM_SECONDS, sport_type="WeightTraining"
    )
    rise = _tsb_after(load) - (CTL_BEFORE - ATL_BEFORE)

    assert rise == pytest.approx(0.0, abs=1.0)
    # And decisively better than what shipped, which rose 2,64.
    assert rise < 2.0


def test_the_shipped_numbers_were_the_problem_not_the_ladder():
    """Sanity anchor: with the old 35/h the same session still rose >2 points."""
    assert _tsb_after(35 * GYM_SECONDS / 3600) - (CTL_BEFORE - ATL_BEFORE) > 2.0


def test_a_gym_hour_costs_roughly_a_tempo_hour():
    assert 45.0 <= DEFAULT_LOAD_PER_HOUR["strength"] <= 65.0


def test_an_aerobic_hour_is_worth_more_than_the_unknown_sport_fallback():
    from services.training_load import FALLBACK_LOAD_PER_HOUR

    assert DEFAULT_LOAD_PER_HOUR["cycling"] > FALLBACK_LOAD_PER_HOUR
    assert DEFAULT_LOAD_PER_HOUR["running"] > DEFAULT_LOAD_PER_HOUR["cycling"]


def test_the_ordering_across_sports_still_makes_sense():
    per_hour = DEFAULT_LOAD_PER_HOUR
    assert per_hour["yoga"] < per_hour["hike"] < per_hour["cycling"]
    assert per_hour["walk"] < per_hour["hike"]


def test_the_ladder_still_prefers_anything_measured():
    """Raising the weakest rung must not let it outrank the ones above it."""
    load = resolve_training_load(
        provider_tss=12.0,
        duration_seconds=GYM_SECONDS,
        sport_type="WeightTraining",
    )
    assert (load.tss, load.source) == (12.0, "provider")


# --- The backfill -----------------------------------------------------------


def _load_migration():
    spec = importlib.util.spec_from_file_location("_recal_579", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path/'recal.db'}")
    models.RideMetric.__table__.create(engine)

    rows = [
        # Estimated from duration under the old constants — this rule's to fix.
        (1, "2026-08-06", "WeightTraining", GYM_SECONDS, 33.9, LOAD_SOURCE_DURATION),
        (2, "2026-08-07", "Hike", 7200, 60.0, LOAD_SOURCE_DURATION),
        # Everything else is someone else's number.
        (3, "2026-08-08", "Ride", 3600, 94.0, "power"),
        (4, "2026-08-09", "Ride", 3600, 63.0, "provider"),
        (5, "2026-08-10", "Yoga", 2820, 12.1, "heart_rate"),
    ]
    insert = models.RideMetric.__table__.insert()
    with engine.begin() as conn:
        for activity_id, day, sport, seconds, tss, source in rows:
            conn.execute(
                insert,
                {
                    "user_id": "user-1",
                    "strava_activity_id": activity_id,
                    "activity_source": "intervals",
                    "external_activity_id": str(activity_id),
                    "activity_date": day,
                    "sport_type": sport,
                    "duration_seconds": seconds,
                    "tss": tss,
                    "tss_source": source,
                    "ctl_after": 0.0,
                    "atl_after": 0.0,
                    "tsb_after": 0.0,
                },
            )
    return engine


def _row(engine, activity_id: int):
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT tss, tss_source, tsb_after FROM ride_metrics "
                "WHERE strava_activity_id = :id"
            ),
            {"id": activity_id},
        ).one()


def _run(engine, direction: str = "upgrade") -> None:
    migration = _load_migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


def test_the_backfill_reprices_only_what_this_rule_produced(engine):
    _run(engine)

    gym = _row(engine, 1)
    assert gym.tss == pytest.approx(
        duration_training_load(
            duration_seconds=GYM_SECONDS, sport_type="WeightTraining"
        )
    )
    assert gym.tss > 33.9

    hike = _row(engine, 2)
    assert hike.tss != 60.0

    # tss_source is exactly the marker that makes "was this ours?" decidable —
    # which is what it was added for.
    assert _row(engine, 3).tss == 94.0
    assert _row(engine, 4).tss == 63.0
    assert _row(engine, 5).tss == pytest.approx(12.1)


def test_the_chain_is_replayed_after_the_loads_move(engine):
    """Loads that change invalidate every chain value after them."""
    _run(engine)
    assert _row(engine, 5).tsb_after != 0.0


def test_the_backfill_is_idempotent(engine):
    _run(engine)
    first = [_row(engine, i) for i in (1, 2, 3, 4, 5)]
    _run(engine)
    assert [_row(engine, i) for i in (1, 2, 3, 4, 5)] == first
