"""Sport as a planning dimension (#710).

``sport_type`` was stored on every imported activity and rendered by the
frontend, but a *plan* could not express it: ``PlanDay`` carried ``workoutType``
and nothing else, so "Tuesday is a run" was unsayable and the ride↔plan matcher
had only one sport-ish signal to go on — ``workoutType == "strength"``.

These lock in the four things that had to hold for the dimension to be real:
the field survives the persist gate, a plan written before the field existed
still reads as the cycling plan it is, a planned sport and a logged sport have
to agree before the two count as one session, and a run is finally visible to
the motivation model.
"""

from __future__ import annotations

import pytest

import crud
import models
import schemas
from auth import hash_password
from services import plan_pipeline, ride_matching
from services.activity_identity import training_sport
from services.training_utility import modality_for_sport
from tests.conftest import TestSessionLocal

DATE = "2026-10-06"


def _session(
    date: str = DATE,
    *,
    sport: str | None = None,
    workout_type: str = "endurance",
    slot: int | None = None,
    duration: int = 60,
) -> dict:
    day: dict = {
        "date": date,
        "workoutType": workout_type,
        "title": f"{sport or 'cycling'} {workout_type}",
        "description": "Session",
        "durationMinutes": duration,
    }
    if sport is not None:
        day["sport"] = sport
    if slot is not None:
        day["slot"] = slot
    return day


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


async def _commit(user_id: str, plan: list[dict], base: list[dict]) -> list[dict]:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, plan, base_plan=base, source="generate"
        )
        await db.commit()
    async with TestSessionLocal() as db:
        row = await crud.get_training_plan(db, user_id)
        return row.plan if row is not None else []


# ---------------------------------------------------------------------------
# The field survives the gate, and a legacy plan is unchanged by its existence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_planned_run_keeps_its_sport_through_the_persist_gate():
    user_id = await _create_user("planned-run@example.com", [])
    saved = await _commit(user_id, [_session(sport="run", duration=45)], [])

    assert len(saved) == 1
    assert saved[0]["sport"] == "running"
    assert schemas.day_sport(saved[0]) == "running"


@pytest.mark.asyncio
async def test_a_legacy_day_with_no_sport_reads_back_as_cycling():
    user_id = await _create_user("legacy-sport@example.com", [])
    saved = await _commit(user_id, [_session()], [])

    assert schemas.day_sport(saved[0]) == "cycling"


@pytest.mark.asyncio
async def test_a_cycling_day_is_still_stored_without_a_sport_key():
    """Storage stays byte-stable, the way slot 0 does (#496).

    Every stored plan predates the field and is implicitly cycling. Emitting
    ``"cycling"`` would rewrite all of them on the first commit and — worse —
    make an unchanged plan compare unequal to the database, turning every no-op
    write into a real one that cascades a login-summary refresh and a
    ride-snapshot rebuild.
    """
    user_id = await _create_user("byte-stable-sport@example.com", [])
    saved = await _commit(user_id, [_session(sport="cycling")], [])

    assert "sport" not in saved[0]
    assert schemas.day_sport(saved[0]) == "cycling"


def test_an_unreadable_sport_never_drops_the_day():
    """A sport is part of a session's meaning, not a gate it can fail."""
    day = schemas.PlanDay.model_validate(
        {"date": DATE, "sport": {"nonsense": 1}, "durationMinutes": 60}
    )
    assert day.sport == "cycling"
    assert day.duration_minutes == 60


def test_a_sport_the_planner_cannot_write_is_kept_rather_than_relabelled():
    """Swimming has no plan support, but an athlete's own edit is not cycling."""
    assert schemas.PlanDay.model_validate({"date": DATE, "sport": "Swim"}).sport == "swim"


def test_day_sport_reads_every_shape_a_plan_day_arrives_in():
    """Total over the three shapes, like ``day_slot``: a stored dict, a validated
    ``PlanDay``, and an ORM-ish object carrying the attribute. Callers hand it
    whichever they have, and a sport is not something any of them may fail on."""
    assert schemas.day_sport(schemas.PlanDay.model_validate(_session(sport="run"))) == "running"
    assert schemas.day_sport({"date": DATE, "sport": "WeightTraining"}) == "strength"

    class _Row:
        sport = "Run"

    assert schemas.day_sport(_Row()) == "running"
    # Nothing to read is the cycling session every pre-#710 caller meant.
    assert schemas.day_sport(object()) == "cycling"
    assert schemas.day_sport(None) == "cycling"


def test_day_sport_accepts_the_provider_spelling_of_the_key():
    """Ride snapshots and imported-activity dicts spell it ``sportType``."""
    assert schemas.day_sport({"date": DATE, "sportType": "Run"}) == "running"
    assert schemas.day_sport({"date": DATE, "sport_type": "Run"}) == "running"


def test_a_plan_update_can_change_the_sport_and_omission_leaves_it_alone():
    day = schemas.PlanDay.model_validate(_session(sport="run"))

    changed = schemas.merge_update(
        day, schemas.PlanDayUpdateSchema.model_validate({"date": DATE, "sport": "ride"})
    )
    assert changed.sport == "cycling"

    untouched = schemas.merge_update(
        day, schemas.PlanDayUpdateSchema.model_validate({"date": DATE, "title": "Hills"})
    )
    assert untouched.sport == "running"


# ---------------------------------------------------------------------------
# One vocabulary on both sides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider_sport,expected",
    [
        ("Run", "running"),
        ("TrailRun", "running"),
        ("VirtualRide", "cycling"),
        ("WeightTraining", "strength"),
        ("Yoga", "strength"),
        ("Hike", "hike"),
        ("", None),
        (None, None),
    ],
)
def test_planned_and_logged_sports_share_one_vocabulary(provider_sport, expected):
    assert training_sport(provider_sport) == expected


def test_running_resolves_to_a_modality():
    """It returned ``None`` before, so every run fell out of behaviour scoring."""
    assert modality_for_sport("Run") is not None
    assert modality_for_sport("Run") == modality_for_sport("TrailRun")
    assert modality_for_sport("Run") != modality_for_sport("Ride")


# ---------------------------------------------------------------------------
# Matching: a planned sport and a logged sport have to agree
# ---------------------------------------------------------------------------


async def _run_match(
    user_id: str, plan: list[dict], activities: list[tuple[int, str, int]]
) -> dict[int, models.RideMetric]:
    """Seed ``(id, sport_type, minutes)`` activities, match them, return by id.

    Start times are an hour apart so the day's activities are never read as one
    session saved in parts (#543).
    """
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        for index, (activity_id, sport_type, minutes) in enumerate(activities):
            await crud.upsert_ride_metric(
                db,
                user_id,
                strava_activity_id=activity_id,
                activity_source="intervals",
                external_activity_id=f"i{activity_id}",
                activity_date=DATE,
                activity_start_datetime=f"{DATE}T{7 + index * 4:02d}:00:00",
                sport_type=sport_type,
                duration_seconds=minutes * 60,
            )
        await db.commit()
        await ride_matching.apply_ride_plan_matches(
            db, user_id, plan, [a[0] for a in activities]
        )
        await db.commit()

    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_date(db, user_id, DATE)
        return {ride.strava_activity_id: ride for ride in rides}


@pytest.mark.asyncio
async def test_a_logged_run_matches_a_planned_run():
    user_id = await _create_user("run-matches@example.com", [])
    plan = [_session(sport="run", duration=45)]
    rides = await _run_match(user_id, plan, [(8001, "Run", 44)])

    assert rides[8001].plan_match_status == ride_matching.MATCH_AUTO
    assert rides[8001].matched_plan_date == DATE


@pytest.mark.asyncio
async def test_a_ride_does_not_match_a_planned_run():
    """The failure this issue exists to stop: before the plan had a sport, a
    planned session accepted any non-gym activity, so an hour on the bike
    completed the athlete's run."""
    user_id = await _create_user("ride-not-run@example.com", [])
    plan = [_session(sport="run", duration=45)]
    rides = await _run_match(user_id, plan, [(8002, "Ride", 44)])

    assert rides[8002].plan_match_status == ride_matching.MATCH_UNMATCHED
    assert rides[8002].matched_plan_date is None
    # It was never a candidate for this session, so it carries no verdict about
    # one — no "additional", no "too much".
    assert rides[8002].label_override is None


@pytest.mark.asyncio
async def test_a_run_does_not_match_a_planned_ride():
    user_id = await _create_user("run-not-ride@example.com", [])
    plan = [_session(duration=45)]
    rides = await _run_match(user_id, plan, [(8003, "Run", 44)])

    assert rides[8003].plan_match_status == ride_matching.MATCH_UNMATCHED


@pytest.mark.asyncio
async def test_a_legacy_strength_day_still_accepts_a_gym_activity():
    """``workoutType: "strength"`` was the one sport the old vocabulary could
    state, so a plan written before #710 must keep matching gym sessions."""
    user_id = await _create_user("legacy-strength@example.com", [])
    plan = [_session(workout_type="strength", duration=60)]
    rides = await _run_match(user_id, plan, [(8004, "WeightTraining", 58)])

    assert rides[8004].plan_match_status == ride_matching.MATCH_AUTO


@pytest.mark.asyncio
async def test_a_two_a_day_of_two_sports_matches_each_session_to_its_own_sport():
    """Gym in the morning, ride in the evening — the case ``slot`` exists for."""
    user_id = await _create_user("gym-and-ride@example.com", [])
    plan = [
        _session(sport="strength", workout_type="strength", slot=0, duration=45),
        _session(sport="cycling", workout_type="endurance", slot=1, duration=90),
    ]
    rides = await _run_match(
        user_id, plan, [(8005, "WeightTraining", 44), (8006, "Ride", 88)]
    )

    assert rides[8005].plan_match_status == ride_matching.MATCH_AUTO
    assert schemas.normalize_slot(rides[8005].matched_plan_slot) == 0
    assert rides[8006].plan_match_status == ride_matching.MATCH_AUTO
    assert schemas.normalize_slot(rides[8006].matched_plan_slot) == 1


@pytest.mark.asyncio
async def test_an_activity_with_no_sport_type_stays_eligible():
    """A missing sport type is a genuine unknown, not a claim that the activity
    was not the planned session — the same reading the ride classifier takes."""
    user_id = await _create_user("unknown-sport@example.com", [])
    plan = [_session(duration=60)]
    rides = await _run_match(user_id, plan, [(8007, "", 58)])

    assert rides[8007].plan_match_status == ride_matching.MATCH_AUTO
