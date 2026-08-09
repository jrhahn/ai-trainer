"""How far apart two recordings may be and still be one planned session.

There is no single right answer, because "one session" means different things
depending on what the session was for.

For a recovery spin, continuity is not the stimulus — blood flow and low-load
movement do not care whether the hour came in one piece, and the evidence that
active recovery beats plain rest at all is thin. Two half-hours three hours apart
are the session.

For an endurance ride, continuity *is* the stimulus: progressive glycogen
depletion, the shift toward fat oxidation, and durability — holding power after
accumulated work. Two fresh hours never produce the state the adaptation comes
from, so the window there is *tighter* than the old global 90 minutes, not
looser. What it exists to forgive is a café stop, and a real café stop is half an
hour.
"""

from __future__ import annotations

import pytest

import crud
from services.ride_matching import (
    EASY_SPLIT_SESSION_MAX_GAP_SECONDS,
    ENDURANCE_SPLIT_SESSION_MAX_GAP_SECONDS,
    MATCH_AUTO,
    MATCH_UNMATCHED,
    _combined_duration_ratios,
    _is_easy_plan,
    _split_session_max_gap,
    apply_ride_plan_matches,
)
from tests.conftest import TestSessionLocal

DATE = "2026-08-09"

RECOVERY_SPIN = {
    "date": DATE,
    # The production plan: the type says endurance, the title says recovery.
    "workoutType": "endurance",
    "title": "Short Heat-Friendly Recovery Spin",
    "description": "75 minutes of gentle recovery riding at 110-145W.",
    "durationMinutes": 75,
    "durationMinMinutes": 60,
    "durationMaxMinutes": 90,
}

LONG_ENDURANCE = {
    "date": DATE,
    "workoutType": "endurance",
    "title": "Long Aerobic Ride",
    "description": "Three hours of steady aerobic riding, easy pace throughout.",
    "durationMinutes": 180,
    "durationMinMinutes": 165,
    "durationMaxMinutes": 210,
}


# --- Which sessions the distinction applies to ------------------------------


def test_the_purpose_is_read_from_the_plan_text_not_only_the_type():
    """The production day said ``workoutType: endurance`` and meant recovery."""
    assert _is_easy_plan(RECOVERY_SPIN)
    assert _split_session_max_gap(RECOVERY_SPIN) == EASY_SPLIT_SESSION_MAX_GAP_SECONDS


def test_a_long_ride_that_calls_itself_easy_is_not_a_recovery_session():
    """The false positive this rule is built to avoid. Plenty of endurance plans
    describe themselves as easy aerobic riding; handing those the whole-day
    window would defeat the entire distinction."""
    assert not _is_easy_plan(LONG_ENDURANCE)
    assert (
        _split_session_max_gap(LONG_ENDURANCE)
        == ENDURANCE_SPLIT_SESSION_MAX_GAP_SECONDS
    )


@pytest.mark.parametrize(
    "title", ["Yoga Flow", "Mobility Routine", "Shakeout Spin", "Regeneration Ride"]
)
def test_the_markers_name_the_purpose(title):
    assert _is_easy_plan({"workoutType": "endurance", "title": title})


def test_the_endurance_window_is_tighter_than_the_old_global_one():
    """A café stop is half an hour. Ninety minutes is long enough to eat, shower
    and start again, which is a second outing rather than an interrupted ride."""
    assert ENDURANCE_SPLIT_SESSION_MAX_GAP_SECONDS < 90 * 60
    assert EASY_SPLIT_SESSION_MAX_GAP_SECONDS > 90 * 60


def test_an_easy_session_is_judged_more_loosely_on_duration_too():
    easy_min, easy_max = _combined_duration_ratios(RECOVERY_SPIN)
    hard_min, hard_max = _combined_duration_ratios(LONG_ENDURANCE)
    assert easy_min < hard_min
    assert easy_max > hard_max


# --- End to end -------------------------------------------------------------


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


async def _seed(user_id: str, plan_day: dict, rides: list[tuple[int, str, int]]):
    """``rides`` is ``(id, start_time, minutes)``; all cycling."""
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [plan_day])
        for activity_id, start, minutes in rides:
            await crud.upsert_ride_metric(
                db,
                user_id,
                strava_activity_id=activity_id,
                activity_date=DATE,
                activity_start_datetime=f"{DATE}T{start}",
                sport_type="Ride",
                duration_seconds=minutes * 60,
            )
        await db.commit()
        await apply_ride_plan_matches(db, user_id, [plan_day], [r[0] for r in rides])
        await db.commit()

    async with TestSessionLocal() as db:
        stored = await crud.get_ride_metrics_by_date(db, user_id, DATE)
        return {ride.strava_activity_id: ride for ride in stored}


@pytest.mark.asyncio
async def test_two_short_recovery_rides_three_hours_apart_are_the_session(
    client, auth_headers
):
    """The production case. 13:39 and 16:53, 34 and 32 minutes, against a planned
    60–90 minute recovery spin: 66 minutes of easy riding is that session, and
    the three hours in between say nothing about whether the legs got flushed."""
    user_id = await _current_user_id(client, auth_headers)
    rides = await _seed(
        user_id, RECOVERY_SPIN, [(7002, "13:39:26", 34), (7003, "16:53:29", 32)]
    )

    assert rides[7002].plan_match_status == MATCH_AUTO
    assert rides[7003].plan_match_status == MATCH_AUTO


@pytest.mark.asyncio
async def test_two_endurance_rides_three_hours_apart_are_not_one_long_ride(
    client, auth_headers
):
    """Same gap, different session, opposite answer. Two fresh 90-minute rides
    are not a three-hour ride: neither reaches the depleted state the long ride
    exists to train, so only one can take the slot and the other is extra."""
    user_id = await _current_user_id(client, auth_headers)
    rides = await _seed(
        user_id, LONG_ENDURANCE, [(7004, "09:00:00", 90), (7005, "13:00:00", 90)]
    )

    statuses = {rides[7004].plan_match_status, rides[7005].plan_match_status}
    assert statuses == {MATCH_AUTO, MATCH_UNMATCHED}


@pytest.mark.asyncio
async def test_an_endurance_ride_interrupted_by_a_cafe_stop_is_still_one_ride(
    client, auth_headers
):
    """The case the window exists for: stopped at 10:30, restarted at 10:55."""
    user_id = await _current_user_id(client, auth_headers)
    rides = await _seed(
        user_id, LONG_ENDURANCE, [(7006, "09:00:00", 90), (7007, "10:55:00", 90)]
    )

    assert rides[7006].plan_match_status == MATCH_AUTO
    assert rides[7007].plan_match_status == MATCH_AUTO


@pytest.mark.asyncio
async def test_an_hour_off_the_bike_is_a_second_endurance_outing(client, auth_headers):
    """The behavioural half of the tightening, not just a smaller constant.

    Stopped at 10:30, back on at 11:35: an hour is long enough to eat and reset,
    so the second ride starts fresh and never reaches the state a long ride
    exists to train. Under the old global 90-minute window these were combined.
    """
    user_id = await _current_user_id(client, auth_headers)
    rides = await _seed(
        user_id, LONG_ENDURANCE, [(7010, "09:00:00", 90), (7011, "11:35:00", 90)]
    )

    statuses = {rides[7010].plan_match_status, rides[7011].plan_match_status}
    assert statuses == {MATCH_AUTO, MATCH_UNMATCHED}


@pytest.mark.asyncio
async def test_a_commute_pair_still_fails_whatever_the_session(client, auth_headers):
    """Nine hours apart is two trainings by any reading (#543)."""
    user_id = await _current_user_id(client, auth_headers)
    rides = await _seed(
        user_id, RECOVERY_SPIN, [(7008, "07:30:00", 35), (7009, "17:30:00", 35)]
    )

    statuses = {rides[7008].plan_match_status, rides[7009].plan_match_status}
    assert MATCH_UNMATCHED in statuses
