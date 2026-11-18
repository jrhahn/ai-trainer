"""A trip without the bike rules out rides only (ai-trainer-ops#136).

A trip said with the bike at home and "kein strukturiertes Training – nur Gehen
und ein paar Wanderungen" blocked every day of it, so the hikes the athlete
asked for could not go into the plan. The owner decided:
such a trip blocks rides, and a walk, a hike or a gym session still fits.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

import models
from domain.availability import (
    NO_RIDING,
    NO_RIDING_REASON,
    absence_without_dates,
    constraint_label,
    extract_availability_constraints,
    says_rides_only,
)
from services import coach_chat, plan_maintenance, plan_translations
from services.plan_constraints import (
    NO_BIKE_FOCUS,
    NO_BIKE_TITLE,
    day_violates_constraint,
    describe_constraint_overrides,
    sanitize_plan_for_constraints,
)
from tests.conftest import TestSessionLocal
from tests.test_nightly_blocked_days_1302 import (  # noqa: F401 (fixture)
    _by_date,
    _iso,
    model,
)
from tests.test_nightly_plan_translations_271 import NOW
from tests.test_plan_maintenance import _create_user_with_plan, _day, _get_plan

TODAY = date(2026, 11, 18)
WALKING_TRIP = (
    "Vom 2. bis 10. Dezember bin ich auf Reise, kein Rad dabei, kein "
    "strukturiertes Training – nur Gehen und ein paar Wanderungen."
)


def _types(text: str) -> set[str]:
    return {
        c["constraint_type"]
        for c in extract_availability_constraints(text, today=TODAY)
        if c["constraint_date"]
    }


@pytest.mark.parametrize(
    "message",
    [
        WALKING_TRIP,
        "Vom 5. bis 12. Dezember im Urlaub, kein Rad dabei.",
        "Vom 5. bis 12. Dezember auf Reise, das Rad bleibt zu Hause, nur Krafttraining.",
        "From 5 to 12 December on holiday without my bike, only hiking.",
        # Something done instead outweighs "no training" in the same sentence.
        "Vom 5. bis 12. Dezember auf Reise, ohne Rad, kein Training, nur wandern.",
    ],
)
def test_a_trip_without_the_bike_blocks_rides_only(message):
    constraints = extract_availability_constraints(message, today=TODAY)

    assert constraints
    assert {c["constraint_type"] for c in constraints} == {NO_RIDING}
    assert {c["reason"] for c in constraints} == {NO_RIDING_REASON}
    assert says_rides_only(message)


def test_the_trip_keeps_its_days():
    days = [
        c["constraint_date"]
        for c in extract_availability_constraints(WALKING_TRIP, today=TODAY)
    ]

    assert days[0] == "2026-12-02"
    assert days[-1] == "2026-12-10"
    assert len(days) == 9


@pytest.mark.parametrize(
    "message",
    [
        "Vom 5. bis 12. Dezember im Urlaub.",
        "Vom 5. bis 12. Dezember auf Reise, kein Rad, kein Training.",
        "From 5 to 12 December away without my bike, I can't train at all.",
        # What is done instead is said not to happen.
        "Vom 5. bis 12. Dezember auf Reise, kein Rad, kein Training, nicht wandern.",
    ],
)
def test_a_trip_that_rules_out_training_blocks_the_days(message):
    assert _types(message) == {"no_training"}
    assert not says_rides_only(message)


def test_a_trip_with_the_bike_blocks_nothing():
    assert _types("Vom 5. bis 12. Dezember im Urlaub auf Mallorca, mit Rad!") == set()


def test_one_day_away_without_the_bike_blocks_its_ride():
    (saturday,) = extract_availability_constraints(
        "Samstag bin ich weg, kein Rad dabei, nur wandern.", today=TODAY
    )

    assert saturday["constraint_type"] == NO_RIDING
    assert saturday["constraint_date"] == "2026-11-21"
    assert (
        constraint_label(NO_RIDING, "2026-11-21") == "No rides on Saturday 2026-11-21"
    )


@pytest.mark.parametrize(
    "message",
    [
        "Samstag bin ich weg.",
        # Something else said about the day rules it out whole.
        "Samstag bin ich weg, kein Rad dabei, und hab keine Zeit für Training.",
    ],
)
def test_one_day_away_otherwise_blocks_the_day(message):
    assert _types(message) == {"no_training"}


def test_a_trip_without_the_bike_is_an_absence_with_dates():
    assert not absence_without_dates(WALKING_TRIP, today=TODAY)


def _no_riding(day: str) -> dict:
    return {"constraintType": NO_RIDING, "constraintDate": day, "weekday": "friday"}


RIDE = {
    "date": "2026-11-20",
    "workoutType": "endurance",
    "title": "Endurance ride",
    "durationMinutes": 120,
    "durationMinMinutes": 100,
    "durationMaxMinutes": 140,
    "targetPower": {"min": 150, "max": 190},
}
HIKE = {
    "date": "2026-11-20",
    "sport": "walking",
    "slot": 1,
    "workoutType": "endurance",
    "title": "Hike",
    "durationMinutes": 300,
}
GYM = {
    "date": "2026-11-21",
    "workoutType": "strength",
    "title": "Gym",
    "durationMinutes": 45,
}


def test_a_ride_on_a_day_without_the_bike_becomes_rest():
    (rested,) = sanitize_plan_for_constraints([RIDE], [_no_riding("2026-11-20")])

    assert rested["workoutType"] == "rest"
    assert rested["title"] == "No bike"
    assert rested["durationMinutes"] == 0
    assert rested.get("durationMinMinutes") is None
    assert rested.get("targetPower") is None
    # Applied again, the day stays as the first pass wrote it.
    assert sanitize_plan_for_constraints([rested], [_no_riding("2026-11-20")]) == [
        rested
    ]


def test_a_walk_and_a_gym_session_stay():
    constraints = [_no_riding("2026-11-20"), _no_riding("2026-11-21")]

    kept = sanitize_plan_for_constraints([HIKE, GYM], constraints)

    assert [(d["title"], d["durationMinutes"]) for d in kept] == [
        ("Hike", 300),
        ("Gym", 45),
    ]
    assert not day_violates_constraint(HIKE, constraints)
    assert not day_violates_constraint(GYM, constraints)


def test_a_rest_day_without_the_bike_is_left_as_it_is():
    rest = {
        "date": "2026-11-20",
        "workoutType": "rest",
        "title": "Rest",
        "durationMinutes": 0,
    }

    (kept,) = sanitize_plan_for_constraints([rest], [_no_riding("2026-11-20")])

    assert kept["title"] == "Rest"


def test_a_required_long_ride_is_not_put_back_on_a_day_without_the_bike():
    constraints = [
        _no_riding("2026-11-20"),
        {
            "constraintType": "required_workout",
            "constraintDate": "2026-11-20",
            "requiredWorkout": {"workoutType": "endurance", "minDurationMinutes": 180},
        },
    ]

    plan = sanitize_plan_for_constraints([RIDE, HIKE], constraints)

    assert [(d["title"], d["durationMinutes"]) for d in plan] == [
        ("No bike", 0),
        ("Hike", 300),
    ]
    # A walk alone on the day is not made into the long ride either.
    (walk,) = sanitize_plan_for_constraints(
        [{**HIKE, "slot": 0, "durationMinutes": 60}], constraints
    )
    assert (walk["title"], walk["durationMinutes"]) == ("Hike", 60)


def test_a_ride_the_coach_asks_for_is_reported_as_no_bike():
    constraints = [_no_riding("2026-11-20")]

    assert describe_constraint_overrides([RIDE, HIKE], constraints) == [
        {
            "date": "2026-11-20",
            "weekday": "friday",
            "constraintType": NO_RIDING,
            "requiredType": None,
        }
    ]


def test_a_day_blocked_whole_is_reported_as_no_training():
    constraints = [
        _no_riding("2026-11-20"),
        {"constraintType": "no_training", "constraintDate": "2026-11-20"},
    ]

    (override,) = describe_constraint_overrides([RIDE], constraints)

    assert override["constraintType"] == "no_training"


def test_the_note_says_the_bike_is_not_along():
    overrides = [
        {"date": "2026-11-20", "weekday": "friday", "constraintType": NO_RIDING}
    ]

    german = coach_chat._format_constraint_override_note(overrides, german=True)
    english = coach_chat._format_constraint_override_note(overrides, german=False)

    assert german.startswith("Für Freitag")
    assert "keine Fahrt" in german
    assert "kein Rad" in german
    assert english.startswith("I couldn't schedule a ride on Friday")
    assert "hike" in english


@pytest.mark.asyncio
async def test_the_night_rests_a_ride_left_on_a_day_without_the_bike(model):  # noqa: F811
    away = _iso(4)
    # Two training days in a row at most, so the guard on days in a row
    # (rest_days) leaves every one of them alone.
    plan = [
        _day(_iso(i))
        if i % 3
        else {**_day(_iso(i)), "workoutType": "rest", "durationMinutes": 0}
        for i in range(14)
    ]
    plan.append({**HIKE, "date": away})
    user_id = await _create_user_with_plan(email="nobike@example.com", plan=plan)
    async with TestSessionLocal() as db:
        db.add(
            models.AthleteAvailabilityConstraint(
                user_id=user_id,
                constraint_type=NO_RIDING,
                constraint_date=away,
                reason=NO_RIDING_REASON,
                source="chat",
            )
        )
        await db.commit()

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert result.updated == 1
    stored = [d for d in await _get_plan(user_id) if d["date"] == away]
    assert sorted((d["title"], d["durationMinutes"]) for d in stored) == [
        ("Hike", 300),
        ("No bike", 0),
    ]
    assert _by_date(await _get_plan(user_id))[_iso(5)]["durationMinutes"] == 60


def test_the_no_bike_day_reads_in_german():
    fixed = plan_translations.FIXED["de"]

    assert fixed[NO_BIKE_TITLE] == "Kein Rad dabei"
    assert all(text in fixed for text in NO_BIKE_FOCUS)


MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20261118_000001_trip_without_bike_rides_only.py"
)
RANGE_REASON = "Athlete said they are away and cannot train."


def _migrate(engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_rides_only", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


def _rows(engine) -> list[tuple]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                sa.text(
                    "SELECT id, constraint_type, reason FROM "
                    "athlete_availability_constraints ORDER BY id"
                )
            )
        ]


def test_the_migration_moves_stored_trips_without_the_bike(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'trips.db'}")
    models.User.__table__.create(engine)
    models.AthleteAvailabilityConstraint.__table__.create(engine)
    rows = [
        ("1", "no_training", RANGE_REASON, WALKING_TRIP),
        ("2", "no_training", RANGE_REASON, "Vom 5. bis 12. Dezember im Urlaub."),
        (
            "3",
            "no_training",
            RANGE_REASON,
            "Vom 5. bis 12. Dezember auf Reise, kein Rad, kein Training.",
        ),
        # A day said on its own keeps what it was stored as.
        (
            "4",
            "no_training",
            "Athlete said they are unavailable for training.",
            WALKING_TRIP,
        ),
        ("5", "required_workout", RANGE_REASON, WALKING_TRIP),
    ]
    with engine.begin() as conn:
        conn.execute(
            models.User.__table__.insert(),
            [{"id": "u", "email": "a@x.co", "hashed_password": "h"}],
        )
        conn.execute(
            models.AthleteAvailabilityConstraint.__table__.insert(),
            [
                {
                    "id": id_,
                    "user_id": "u",
                    "constraint_type": kind,
                    "constraint_date": "2026-11-20",
                    "reason": reason,
                    "source": source,
                }
                for id_, kind, reason, source in rows
            ],
        )
    before = _rows(engine)

    _migrate(engine, "upgrade")
    _migrate(engine, "upgrade")

    assert _rows(engine) == [
        ("1", NO_RIDING, NO_RIDING_REASON),
        *before[1:],
    ]

    _migrate(engine, "downgrade")
    assert _rows(engine) == before
