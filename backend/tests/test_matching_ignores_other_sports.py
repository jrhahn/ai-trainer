"""One yoga class must not take the day's rides down with it.

Production, 2026-08-09: a planned 60–90 min recovery spin, two road rides and a
yoga session. All three came back ``ambiguous``, so the athlete was asked which
one was the planned ride — including the yoga.

The cause was not that the rides were short. ``_is_duration_focused_ride_plan``
ends in ``all(_is_cycling_ride(ride) for ride in rides)`` over *every* activity
of the day, so the yoga made it false, the whole duration-matching path was
skipped, and everything fell into the ambiguous bucket. The guard that answers
"could this activity be this session at all?" already existed — the two-a-day
path uses it (#496) — and the single-session path never asked.
"""

from __future__ import annotations

import pytest

import crud
from services.ride_matching import (
    MATCH_AMBIGUOUS,
    MATCH_AUTO,
    MATCH_UNMATCHED,
    apply_ride_plan_matches,
)
from tests.conftest import TestSessionLocal

DATE = "2026-08-09"
RECOVERY_SPIN = {
    "date": DATE,
    "workoutType": "endurance",
    "title": "Short Heat-Friendly Recovery Spin",
    "description": "75 minutes of gentle recovery riding at 110-145W.",
    "durationMinutes": 75,
    "durationMinMinutes": 60,
    "durationMaxMinutes": 90,
}


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


async def _seed(user_id: str, activities: list[tuple[int, str, str, int]]) -> None:
    """``activities`` is ``(id, sport_type, start_time, minutes)``."""
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [RECOVERY_SPIN])
        for activity_id, sport_type, start, minutes in activities:
            await crud.upsert_ride_metric(
                db,
                user_id,
                strava_activity_id=activity_id,
                activity_source="intervals",
                external_activity_id=f"i{activity_id}",
                activity_date=DATE,
                activity_start_datetime=f"{DATE}T{start}",
                sport_type=sport_type,
                duration_seconds=minutes * 60,
            )
        await db.commit()
        await apply_ride_plan_matches(
            db, user_id, [RECOVERY_SPIN], [a[0] for a in activities]
        )
        await db.commit()


async def _by_id(user_id: str) -> dict[int, crud.models.RideMetric]:
    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_date(db, user_id, DATE)
        return {ride.strava_activity_id: ride for ride in rides}


# The production day, verbatim.
PROD_DAY = [
    (7001, "Yoga", "10:38:48", 47),
    (7002, "Ride", "13:39:26", 34),
    (7003, "Ride", "16:53:29", 32),
]


@pytest.mark.asyncio
async def test_the_yoga_session_is_not_asked_which_bike_ride_it_was(
    client, auth_headers
):
    user_id = await _current_user_id(client, auth_headers)
    await _seed(user_id, PROD_DAY)

    yoga = (await _by_id(user_id))[7001]
    assert yoga.plan_match_status == MATCH_UNMATCHED


@pytest.mark.asyncio
async def test_the_yoga_session_is_not_penalised_for_being_extra(
    client, auth_headers
):
    """It was never a candidate for a cycling session, so it carries no verdict
    about one — no "additional", no "too much"."""
    user_id = await _current_user_id(client, auth_headers)
    await _seed(user_id, PROD_DAY)

    yoga = (await _by_id(user_id))[7001]
    assert yoga.label_override is None
    # And it is not linked to a planned session it could never have been.
    assert yoga.matched_plan_date is None
    assert yoga.matched_plan_snapshot is None


@pytest.mark.asyncio
async def test_the_rides_are_matched_instead_of_all_going_ambiguous(
    client, auth_headers
):
    """The headline symptom. With the yoga out of the candidate list the two
    rides reach the duration path: they start 3 h 14 apart so they are not one
    split session (#543), and the closer one to the planned 75 minutes takes the
    slot while the other reads as an extra ride."""
    user_id = await _current_user_id(client, auth_headers)
    await _seed(user_id, PROD_DAY)

    rides = await _by_id(user_id)
    statuses = {rides[7002].plan_match_status, rides[7003].plan_match_status}
    assert MATCH_AMBIGUOUS not in statuses
    assert MATCH_AUTO in statuses


@pytest.mark.asyncio
async def test_two_rides_and_no_other_sport_are_unchanged(client, auth_headers):
    """The narrowing must not have swallowed the case it does not touch."""
    user_id = await _current_user_id(client, auth_headers)
    await _seed(user_id, PROD_DAY[1:])

    rides = await _by_id(user_id)
    statuses = {rides[7002].plan_match_status, rides[7003].plan_match_status}
    assert MATCH_AUTO in statuses


@pytest.mark.asyncio
async def test_a_lone_ride_beside_a_yoga_class_still_matches(client, auth_headers):
    """Before the fix this went ambiguous too: two activities, one of them not a
    ride, so `len(date_rides) == 1` was false."""
    user_id = await _current_user_id(client, auth_headers)
    await _seed(user_id, [(7001, "Yoga", "10:38:48", 47), (7002, "Ride", "13:39:26", 70)])

    rides = await _by_id(user_id)
    assert rides[7002].plan_match_status == MATCH_AUTO
    assert rides[7001].plan_match_status == MATCH_UNMATCHED


@pytest.mark.asyncio
async def test_a_day_with_only_other_sports_leaves_the_session_unmatched(
    client, auth_headers
):
    """Nothing to match, and nothing to ask about either."""
    user_id = await _current_user_id(client, auth_headers)
    await _seed(user_id, [(7001, "Yoga", "10:38:48", 47)])

    yoga = (await _by_id(user_id))[7001]
    assert yoga.plan_match_status == MATCH_UNMATCHED


@pytest.mark.asyncio
async def test_a_planned_strength_session_keeps_the_mirror_case(client, auth_headers):
    """The guard cuts both ways: a bike ride is not a candidate for a planned
    gym session, which is what ``_session_accepts_ride`` has always said."""
    user_id = await _current_user_id(client, auth_headers)
    strength_day = {**RECOVERY_SPIN, "workoutType": "strength", "title": "Gym"}
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [strength_day])
        for activity_id, sport_type, minutes in (
            (7001, "WeightTraining", 58),
            (7002, "Ride", 70),
        ):
            await crud.upsert_ride_metric(
                db,
                user_id,
                strava_activity_id=activity_id,
                activity_date=DATE,
                sport_type=sport_type,
                duration_seconds=minutes * 60,
            )
        await db.commit()
        await apply_ride_plan_matches(db, user_id, [strength_day], [7001, 7002])
        await db.commit()

    rides = await _by_id(user_id)
    assert rides[7001].plan_match_status == MATCH_AUTO
    assert rides[7002].plan_match_status == MATCH_UNMATCHED
    assert rides[7002].label_override is None
