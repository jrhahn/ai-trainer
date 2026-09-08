"""The coherence check runs for every writer, not just the coach chat (#665).

#659 and #660 taught the coach to notice colliding days. Both live in the chat
prompt, and `find_repeated_sessions` was called from exactly one place — the
ask-trainer handler — so the two triggers responsible for 92 % of production
plan changes never saw either. This locks in that the check now sits at
`_enforce_and_persist`, the one gate every trigger passes.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
import models
from auth import hash_password
from services import plan_coherence, plan_pipeline
from services.dates import app_today
from tests.conftest import TestSessionLocal


def _day(
    date: str,
    workout_type: str = "endurance",
    title: str | None = None,
    duration: int = 60,
) -> dict:
    return {
        "date": date,
        "workoutType": workout_type,
        "title": title or f"Workout {date}",
        "description": "Session",
        "durationMinutes": duration,
    }


async def _create_user(email: str, plan: list[dict]) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        return user.id


def _dates(count: int = 5, offset: int = 1) -> list[str]:
    return [(app_today() + timedelta(days=offset + i)).isoformat() for i in range(count)]


async def _commit(user_id: str, plan: list[dict], source: str):
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = await crud.get_training_plan(db, user_id)
        base = current.plan if current is not None else []
        result = await plan_pipeline.commit_plan(
            db, user, plan, base_plan=base, source=source
        )
        await db.commit()
        return result


def _by_date(plan: list[dict]) -> dict[str, dict]:
    return {d["date"]: d for d in plan}


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


def test_stacked_strength_is_found_even_when_the_titles_differ():
    """The 2026-09-08 pair: two different strength sessions on adjacent days."""
    a, b = _dates(2)
    stacked = plan_coherence.find_stacked_strength(
        [
            _day(a, "strength", "Core and Upper Body Strength", 45),
            _day(b, "strength", "Core and Upper Body Mobility", 45),
        ],
        app_today().isoformat(),
    )

    assert [s["dates"] for s in stacked] == [[a, b]]
    # find_repeated_sessions sees nothing here — the sessions are not identical.
    assert plan_coherence.find_repeated_sessions(
        [
            _day(a, "strength", "Core and Upper Body Strength", 45),
            _day(b, "strength", "Core and Upper Body Mobility", 45),
        ],
        app_today().isoformat(),
    ) == []


def test_strength_with_a_day_between_is_not_stacked():
    a, _, c = _dates(3)
    plan = [
        _day(a, "strength", "Gym", 45),
        _day(_dates(3)[1], "recovery", "Easy Spin", 45),
        _day(c, "strength", "Gym Again", 45),
    ]
    assert plan_coherence.find_stacked_strength(plan, app_today().isoformat()) == []


def test_collision_keys_distinguish_one_duplicate_from_another():
    """Swapping a duplicate pair for a different one must read as a new collision."""
    a, b = _dates(2)
    today = app_today().isoformat()
    first = [
        _day(a, "endurance", "Steady Ride", 90),
        _day(b, "endurance", "Steady Ride", 90),
    ]
    second = [
        _day(a, "intervals", "Threshold Blocks", 75),
        _day(b, "intervals", "Threshold Blocks", 75),
    ]

    assert plan_coherence.collision_keys(first, today) != plan_coherence.collision_keys(
        second, today
    )


def test_collision_keys_are_empty_for_a_coherent_week():
    days = _dates(4)
    plan = [
        _day(days[0], "strength", "Gym", 45),
        _day(days[1], "endurance", "Steady Ride", 90),
        _day(days[2], "rest", "Rest Day", 0),
        _day(days[3], "rest", "Rest Day", 0),
    ]
    assert plan_coherence.collision_keys(plan, app_today().isoformat()) == set()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_may_not_stack_strength_onto_a_coach_agreed_day():
    """The exact production regression of 2026-09-08.

    Wednesday was pinned by the coach as strength; an automated write turned
    Thursday into a second strength day and every existing guard allowed it.
    """
    wed, thu = _dates(2)
    user_id = await _create_user(
        "gate-stacked@example.com",
        [
            _day(wed, "strength", "Core and Upper Body Strength", 45),
            _day(thu, "intervals", "Sub-Threshold Climbing Intervals", 75),
        ],
    )
    # Pin Wednesday the way coach_chat does.
    await _commit(
        user_id,
        [
            _day(wed, "strength", "Core and Upper Body Strength", 45),
            _day(thu, "intervals", "Sub-Threshold Climbing Intervals", 75),
        ],
        "coach_chat",
    )

    result = await _commit(
        user_id,
        [
            _day(wed, "strength", "Core and Upper Body Strength", 45),
            _day(thu, "strength", "Core and Upper Body Mobility", 45),
        ],
        "generate",
    )

    days = _by_date(result.plan)
    assert days[thu]["workoutType"] == "intervals"
    assert days[thu]["title"] == "Sub-Threshold Climbing Intervals"


@pytest.mark.asyncio
async def test_the_blocked_attempt_is_recorded_in_the_history():
    """A silent revert would be as opaque as the silent write it replaces."""
    wed, thu = _dates(2)
    user_id = await _create_user(
        "gate-history@example.com",
        [
            _day(wed, "strength", "Gym", 45),
            _day(thu, "endurance", "Steady Ride", 90),
        ],
    )

    await _commit(
        user_id,
        [
            _day(wed, "strength", "Gym", 45),
            _day(thu, "strength", "More Gym", 45),
        ],
        "nightly_maintenance",
    )

    async with TestSessionLocal() as db:
        rows = await crud.list_plan_day_history(db, user_id, date=thu)
    blocked = [r for r in rows if not r.applied]
    assert blocked, "the reverted day must leave an applied=False record"
    assert blocked[0].new_day["workoutType"] == "strength"
    assert blocked[0].old_day["workoutType"] == "endurance"


@pytest.mark.asyncio
async def test_the_coach_may_still_stack_strength_when_the_athlete_asks():
    """Guiding, not blocking. #659 already states the conflict in the prompt."""
    wed, thu = _dates(2)
    user_id = await _create_user(
        "gate-coach@example.com",
        [
            _day(wed, "strength", "Gym", 45),
            _day(thu, "endurance", "Steady Ride", 90),
        ],
    )

    result = await _commit(
        user_id,
        [
            _day(wed, "strength", "Gym", 45),
            _day(thu, "strength", "Gym Again", 45),
        ],
        "coach_chat",
    )

    assert _by_date(result.plan)[thu]["workoutType"] == "strength"


@pytest.mark.asyncio
async def test_a_manual_edit_may_stack_strength_too():
    wed, thu = _dates(2)
    user_id = await _create_user(
        "gate-user-edit@example.com",
        [
            _day(wed, "strength", "Gym", 45),
            _day(thu, "endurance", "Steady Ride", 90),
        ],
    )

    result = await _commit(
        user_id,
        [
            _day(wed, "strength", "Gym", 45),
            _day(thu, "strength", "Gym Again", 45),
        ],
        "user_edit",
    )

    assert _by_date(result.plan)[thu]["workoutType"] == "strength"


@pytest.mark.asyncio
async def test_an_automated_write_may_not_duplicate_an_identical_session():
    """#659's case, now enforced rather than merely reported."""
    a, b = _dates(2)
    user_id = await _create_user(
        "gate-repeat@example.com",
        [
            _day(a, "endurance", "Steady Aerobic Ride", 90),
            _day(b, "recovery", "Easy Spin", 45),
        ],
    )

    result = await _commit(
        user_id,
        [
            _day(a, "endurance", "Steady Aerobic Ride", 90),
            _day(b, "endurance", "Steady Aerobic Ride", 90),
        ],
        "generate",
    )

    assert _by_date(result.plan)[b]["title"] == "Easy Spin"


@pytest.mark.asyncio
async def test_a_pre_existing_collision_is_not_unwound_by_an_unrelated_write():
    """The write is answerable for what it created, not for what it inherited."""
    days = _dates(3)
    stacked = [
        _day(days[0], "strength", "Gym", 45),
        _day(days[1], "strength", "Gym Again", 45),
        _day(days[2], "endurance", "Steady Ride", 90),
    ]
    user_id = await _create_user("gate-preexisting@example.com", stacked)

    result = await _commit(
        user_id,
        [
            _day(days[0], "strength", "Gym", 45),
            _day(days[1], "strength", "Gym Again", 45),
            _day(days[2], "endurance", "Longer Steady Ride", 120),
        ],
        "generate",
    )

    days_by_date = _by_date(result.plan)
    # The pre-existing stack survives untouched …
    assert days_by_date[days[0]]["workoutType"] == "strength"
    assert days_by_date[days[1]]["workoutType"] == "strength"
    # … and the unrelated edit went through.
    assert days_by_date[days[2]]["durationMinutes"] == 120


@pytest.mark.asyncio
async def test_the_later_day_is_the_one_handed_back():
    """The earlier day is nearer to being ridden, and likelier the agreed one."""
    a, b = _dates(2)
    user_id = await _create_user(
        "gate-later@example.com",
        [
            _day(a, "endurance", "Steady Ride", 90),
            _day(b, "recovery", "Easy Spin", 45),
        ],
    )

    result = await _commit(
        user_id,
        [
            _day(a, "strength", "Gym", 45),
            _day(b, "strength", "Gym Again", 45),
        ],
        "nightly_maintenance",
    )

    days_by_date = _by_date(result.plan)
    assert days_by_date[a]["workoutType"] == "strength"
    assert days_by_date[b]["title"] == "Easy Spin"


@pytest.mark.asyncio
async def test_a_coherent_automated_rewrite_is_untouched():
    """The guard must be invisible on a normal week."""
    days = _dates(4)
    user_id = await _create_user(
        "gate-clean@example.com",
        [_day(d, "endurance", f"Old {d}", 60) for d in days],
    )

    proposed = [
        _day(days[0], "strength", "Gym", 45),
        _day(days[1], "endurance", "Steady Ride", 90),
        _day(days[2], "intervals", "Threshold Blocks", 75),
        _day(days[3], "rest", "Rest Day", 0),
    ]
    result = await _commit(user_id, proposed, "generate")

    days_by_date = _by_date(result.plan)
    assert [days_by_date[d]["title"] for d in days] == [
        "Gym",
        "Steady Ride",
        "Threshold Blocks",
        "Rest Day",
    ]


@pytest.mark.asyncio
async def test_repeated_rest_days_are_not_a_collision():
    """Two rest days in a row is a taper, not a scheduling slip."""
    a, b = _dates(2)
    user_id = await _create_user(
        "gate-rest@example.com",
        [
            _day(a, "endurance", "Steady Ride", 90),
            _day(b, "endurance", "Another Ride", 90),
        ],
    )

    result = await _commit(
        user_id,
        [_day(a, "rest", "Rest Day", 0), _day(b, "rest", "Rest Day", 0)],
        "generate",
    )

    days_by_date = _by_date(result.plan)
    assert days_by_date[a]["workoutType"] == "rest"
    assert days_by_date[b]["workoutType"] == "rest"


@pytest.mark.asyncio
async def test_a_collision_created_by_appending_new_days_is_left_alone():
    """A day with no previous version cannot be handed back, and deleting it
    would cost the athlete a session — the #651 mistake."""
    days = _dates(2)
    user_id = await _create_user(
        "gate-append@example.com", [_day(days[0], "strength", "Gym", 45)]
    )

    result = await _commit(
        user_id,
        [
            _day(days[0], "strength", "Gym", 45),
            _day(days[1], "strength", "Gym Again", 45),
        ],
        "generate",
    )

    assert _by_date(result.plan)[days[1]]["workoutType"] == "strength"
