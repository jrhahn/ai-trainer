"""Tests for multiple sessions per day — two-a-days (#496).

A plan is a flat list where a *date may repeat*: two sessions sharing one date
are distinguished by ``slot``. These lock in the three things that had to change
for that to work: the session identity itself, the pipeline's per-session
keying (pins, completion, history, merge), and ride↔session matching.

They also pin the backward-compatibility contract: a legacy single-workout day
carries no ``slot``, must keep carrying none through a round-trip, and must keep
behaving exactly as it did before.
"""

from __future__ import annotations

import pytest

import crud
import models
import schemas
from auth import hash_password
from services import plan_pipeline, ride_matching
from services.dates import annotate_plan_days
from tests.conftest import TestSessionLocal


def _session(
    date: str,
    workout_type: str = "endurance",
    *,
    slot: int | None = None,
    duration: int = 60,
    completed: bool = False,
    time_of_day: str | None = None,
    title: str | None = None,
) -> dict:
    day: dict = {
        "date": date,
        "workoutType": workout_type,
        "title": title or f"{workout_type} {date}",
        "description": "Session",
        "durationMinutes": duration,
        "completed": completed,
    }
    if slot is not None:
        day["slot"] = slot
    if time_of_day is not None:
        day["timeOfDay"] = time_of_day
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


async def _get_plan(user_id: str) -> list[dict]:
    async with TestSessionLocal() as db:
        row = await crud.get_training_plan(db, user_id)
        return row.plan if row is not None else []


# ---------------------------------------------------------------------------
# Pipeline: a day can hold two sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_holds_two_sessions_on_one_date():
    """AM yoga + PM endurance both survive a commit, each with its own fields."""
    date = "2026-08-04"
    plan = [
        _session(date, "recovery", slot=0, duration=30, time_of_day="am"),
        _session(date, "endurance", slot=1, duration=120, time_of_day="pm"),
    ]
    user_id = await _create_user("two-a-day@example.com", [])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, plan, base_plan=[], source="generate"
        )
        await db.commit()

    saved = await _get_plan(user_id)
    assert len(saved) == 2
    by_slot = {schemas.day_slot(d): d for d in saved}
    assert by_slot[0]["workoutType"] == "recovery"
    assert by_slot[0]["durationMinutes"] == 30
    assert by_slot[1]["workoutType"] == "endurance"
    assert by_slot[1]["durationMinutes"] == 120


@pytest.mark.asyncio
async def test_completing_one_session_leaves_the_other_pending():
    """Ticking the morning yoga must not freeze or complete the evening ride."""
    date = "2026-08-05"
    plan = [
        _session(date, "recovery", slot=0, duration=30, completed=True),
        _session(date, "endurance", slot=1, duration=120),
    ]
    user_id = await _create_user("per-session-complete@example.com", plan)

    # An automated trigger proposes rewriting both sessions.
    proposal = [
        _session(date, "intervals", slot=0, duration=90),
        _session(date, "tempo", slot=1, duration=75),
    ]
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, proposal, base_plan=plan, source="nightly_maintenance"
        )
        await db.commit()

    saved = {schemas.day_slot(d): d for d in await _get_plan(user_id)}
    # The completed AM session is historical fact — untouched.
    assert saved[0]["workoutType"] == "recovery"
    assert saved[0]["completed"] is True
    # The PM session was never completed, so the automated retune lands.
    assert saved[1]["workoutType"] == "tempo"


@pytest.mark.asyncio
async def test_pin_protects_one_session_not_the_whole_day():
    """A user pin guards the session they edited; the other stays adjustable."""
    date = "2026-08-06"
    plan = [
        _session(date, "recovery", slot=0, duration=30),
        _session(date, "endurance", slot=1, duration=120),
    ]
    user_id = await _create_user("per-session-pin@example.com", plan)

    # The athlete edits only the morning session.
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": date, "slot": 0, "workoutType": "strength", "title": "Gym"}],
            base_plan=plan,
            source="user_edit",
        )
        await db.commit()

    pinned_plan = await _get_plan(user_id)
    assert {schemas.day_slot(d): d.get("source") for d in pinned_plan}[0] == "user"

    # An automated trigger then tries to rewrite both sessions.
    proposal = [
        _session(date, "intervals", slot=0, duration=90),
        _session(date, "tempo", slot=1, duration=75),
    ]
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, proposal, base_plan=pinned_plan, source="auto_adapt"
        )
        await db.commit()

    saved = {schemas.day_slot(d): d for d in await _get_plan(user_id)}
    assert saved[0]["workoutType"] == "strength"  # pin held
    assert saved[1]["workoutType"] == "tempo"  # sibling still adjustable


@pytest.mark.asyncio
async def test_history_records_one_row_per_session():
    """A two-a-day's change log is per session, not one conflated row per date."""
    date = "2026-08-07"
    plan = [
        _session(date, "recovery", slot=0, duration=30),
        _session(date, "endurance", slot=1, duration=120),
    ]
    user_id = await _create_user("per-session-history@example.com", plan)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db,
            user,
            [
                _session(date, "recovery", slot=0, duration=30),
                _session(date, "tempo", slot=1, duration=75),
            ],
            base_plan=plan,
            source="adapt",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        rows = await crud.list_plan_day_history(db, user_id, date=date)

    changed = [row for row in rows if row.applied]
    assert len(changed) == 1
    # Only the PM session changed, and the row says so.
    assert changed[0].slot == 1
    assert changed[0].new_day["workoutType"] == "tempo"


@pytest.mark.asyncio
async def test_slotless_update_targets_the_days_first_session():
    """Every pre-#496 caller sends no slot and must keep hitting slot 0."""
    date = "2026-08-08"
    plan = [
        _session(date, "recovery", slot=0, duration=30),
        _session(date, "endurance", slot=1, duration=120),
    ]
    user_id = await _create_user("slotless-update@example.com", plan)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db, user, [{"date": date, "durationMinutes": 45}],
            base_plan=plan, source="user_edit",
        )
        await db.commit()

    saved = {schemas.day_slot(d): d for d in await _get_plan(user_id)}
    assert saved[0]["durationMinutes"] == 45
    assert saved[1]["durationMinutes"] == 120


@pytest.mark.asyncio
async def test_legacy_single_session_plan_round_trips_without_a_slot():
    """A stored plan written before #496 must come back byte-identical."""
    plan = [_session("2026-08-09", "endurance"), _session("2026-08-10", "rest")]
    user_id = await _create_user("legacy-roundtrip@example.com", plan)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, plan, base_plan=plan, source="generate"
        )
        await db.commit()

    saved = await _get_plan(user_id)
    assert all("slot" not in day for day in saved)
    assert [d["workoutType"] for d in saved] == ["endurance", "rest"]


# ---------------------------------------------------------------------------
# Ride ↔ session matching
# ---------------------------------------------------------------------------


async def _add_ride(
    user_id: str,
    activity_id: int,
    date: str,
    *,
    sport_type: str,
    duration_seconds: int,
    start: str | None = None,
    intensity_factor: float | None = None,
) -> None:
    async with TestSessionLocal() as db:
        db.add(
            models.RideMetric(
                user_id=user_id,
                strava_activity_id=activity_id,
                activity_date=date,
                activity_start_datetime=start,
                sport_type=sport_type,
                duration_seconds=duration_seconds,
                intensity_factor=intensity_factor,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_two_activities_match_their_own_sessions():
    """The morning gym session and the evening ride each attach to their session.

    Before #496 the day held one plan entry, so exactly one activity could match
    and the other was forced to MATCH_UNMATCHED.
    """
    date = "2026-08-13"
    plan = [
        _session(date, "strength", slot=0, duration=45, time_of_day="am"),
        _session(date, "endurance", slot=1, duration=120, time_of_day="pm"),
    ]
    user_id = await _create_user("two-activities@example.com", plan)
    await _add_ride(
        user_id, 9001, date,
        sport_type="WeightTraining", duration_seconds=45 * 60,
        start=f"{date}T07:00:00Z",
    )
    await _add_ride(
        user_id, 9002, date,
        sport_type="Ride", duration_seconds=120 * 60,
        start=f"{date}T18:00:00Z",
    )

    async with TestSessionLocal() as db:
        await ride_matching.apply_ride_plan_matches(db, user_id, plan, [9001, 9002])
        await db.commit()

    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_date(db, user_id, date)

    by_id = {r.strava_activity_id: r for r in rides}
    assert by_id[9001].plan_match_status == ride_matching.MATCH_AUTO
    assert by_id[9001].matched_plan_slot == 0
    assert by_id[9001].matched_plan_snapshot["workoutType"] == "strength"
    assert by_id[9002].plan_match_status == ride_matching.MATCH_AUTO
    assert by_id[9002].matched_plan_slot == 1
    assert by_id[9002].matched_plan_snapshot["workoutType"] == "endurance"


@pytest.mark.asyncio
async def test_extra_activity_on_a_two_a_day_stays_cleanly_unmatched():
    """A third ride has no session left, so it is an extra — not a false match."""
    date = "2026-08-14"
    plan = [
        _session(date, "recovery", slot=0, duration=30),
        _session(date, "endurance", slot=1, duration=120),
    ]
    user_id = await _create_user("extra-activity@example.com", plan)
    await _add_ride(user_id, 9101, date, sport_type="Ride", duration_seconds=30 * 60,
                    start=f"{date}T07:00:00Z")
    await _add_ride(user_id, 9102, date, sport_type="Ride", duration_seconds=120 * 60,
                    start=f"{date}T12:00:00Z")
    await _add_ride(user_id, 9103, date, sport_type="Ride", duration_seconds=25 * 60,
                    start=f"{date}T20:00:00Z")

    async with TestSessionLocal() as db:
        await ride_matching.apply_ride_plan_matches(
            db, user_id, plan, [9101, 9102, 9103]
        )
        await db.commit()

    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_date(db, user_id, date)

    by_id = {r.strava_activity_id: r for r in rides}
    matched = [r for r in by_id.values() if r.plan_match_status == ride_matching.MATCH_AUTO]
    assert len(matched) == 2
    assert {r.matched_plan_slot for r in matched} == {0, 1}
    assert by_id[9103].plan_match_status == ride_matching.MATCH_UNMATCHED
    assert by_id[9103].matched_plan_slot is None


@pytest.mark.asyncio
async def test_matched_sessions_complete_independently():
    """Only the sessions actually ridden are marked done."""
    date = "2026-08-15"
    plan = [
        _session(date, "strength", slot=0, duration=45),
        _session(date, "endurance", slot=1, duration=120),
    ]
    user_id = await _create_user("session-completion@example.com", plan)
    await _add_ride(
        user_id, 9201, date, sport_type="WeightTraining",
        duration_seconds=45 * 60, start=f"{date}T07:00:00Z",
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        matched = await ride_matching.apply_ride_plan_matches(
            db, user_id, plan, [9201]
        )
        await ride_matching.mark_matched_days_completed(db, user, plan, matched)
        await db.commit()

    saved = {schemas.day_slot(d): d for d in await _get_plan(user_id)}
    assert saved[0].get("completed") is True
    assert not saved[1].get("completed")


# ---------------------------------------------------------------------------
# Several activities, ONE planned session (#543)
#
# Adding two rides' durations together may only stand in for a planned session
# when they were one ride to begin with. The sum fitting proves nothing on its
# own — a commute plus an evening ride hit it exactly and completed the day.
# ---------------------------------------------------------------------------


async def _match_and_read(user_id: str, plan: list[dict], date: str, ids: list[int]):
    async with TestSessionLocal() as db:
        await ride_matching.apply_ride_plan_matches(db, user_id, plan, ids)
        await db.commit()
    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_date(db, user_id, date)
    return {r.strava_activity_id: r for r in rides}


@pytest.mark.asyncio
async def test_a_ride_split_by_a_stop_counts_as_the_planned_session():
    """Ended at 10:00, restarted at 10:15: two files, one 120-minute session."""
    date = "2026-08-16"
    plan = [_session(date, "endurance", duration=120)]
    user_id = await _create_user("split-session@example.com", plan)
    await _add_ride(user_id, 9301, date, sport_type="Ride",
                    duration_seconds=60 * 60, start=f"{date}T09:00:00Z")
    await _add_ride(user_id, 9302, date, sport_type="Ride",
                    duration_seconds=60 * 60, start=f"{date}T10:15:00Z")

    by_id = await _match_and_read(user_id, plan, date, [9301, 9302])

    assert by_id[9301].plan_match_status == ride_matching.MATCH_AUTO
    assert by_id[9302].plan_match_status == ride_matching.MATCH_AUTO
    # One session, so both halves point at the same one.
    assert by_id[9301].matched_plan_slot == by_id[9302].matched_plan_slot
    assert by_id[9301].label_override == ride_matching.LABEL_OK
    assert by_id[9302].label_override == ride_matching.LABEL_OK


@pytest.mark.asyncio
async def test_a_commute_and_an_evening_ride_do_not_add_up_to_the_session():
    """The regression: 60' + 70' = 130' fits a 120' plan, nine hours apart.

    Before #543 both were marked done. Now the closer-fitting ride takes the
    session and the commute is an extra.
    """
    date = "2026-08-17"
    plan = [_session(date, "endurance", duration=120)]
    user_id = await _create_user("commute-pair@example.com", plan)
    await _add_ride(user_id, 9401, date, sport_type="Ride",
                    duration_seconds=60 * 60, start=f"{date}T07:30:00Z")
    await _add_ride(user_id, 9402, date, sport_type="Ride",
                    duration_seconds=70 * 60, start=f"{date}T17:30:00Z")

    by_id = await _match_and_read(user_id, plan, date, [9401, 9402])

    assert by_id[9402].plan_match_status == ride_matching.MATCH_AUTO
    assert by_id[9401].plan_match_status == ride_matching.MATCH_UNMATCHED
    assert by_id[9401].label_override == ride_matching.LABEL_ADDITIONAL


@pytest.mark.asyncio
async def test_a_hard_extra_ride_is_flagged_as_too_much_not_merely_additional():
    """Same shape, but the ride that missed out was a hard one."""
    date = "2026-08-18"
    plan = [_session(date, "endurance", duration=120)]
    user_id = await _create_user("hard-extra@example.com", plan)
    await _add_ride(user_id, 9501, date, sport_type="Ride",
                    duration_seconds=60 * 60, start=f"{date}T07:30:00Z",
                    intensity_factor=0.9)
    await _add_ride(user_id, 9502, date, sport_type="Ride",
                    duration_seconds=70 * 60, start=f"{date}T17:30:00Z")

    by_id = await _match_and_read(user_id, plan, date, [9501, 9502])

    assert by_id[9502].plan_match_status == ride_matching.MATCH_AUTO
    assert by_id[9501].plan_match_status == ride_matching.MATCH_UNMATCHED
    assert by_id[9501].label_override == ride_matching.LABEL_TOO_MUCH


def test_session_feedback_adds_the_halves_up():
    """Duration is the sum; average power is weighted by it, not averaged."""
    halves = [
        models.RideMetric(duration_seconds=60 * 60, avg_power_w=200, user_note=None),
        models.RideMetric(duration_seconds=30 * 60, avg_power_w=140, user_note=None),
    ]

    feedback = ride_matching._session_feedback_from_metrics(halves)

    assert feedback["actualDurationMinutes"] == 90
    # 200 for an hour and 140 for half of one is 180, not 170.
    assert feedback["averagePower"] == 180


def test_session_feedback_takes_notes_from_the_half_that_has_them():
    halves = [
        models.RideMetric(duration_seconds=60 * 60, avg_power_w=None, user_note=None),
        models.RideMetric(
            duration_seconds=60 * 60, avg_power_w=None, user_note="Legs felt heavy"
        ),
    ]

    feedback = ride_matching._session_feedback_from_metrics(halves)

    assert feedback["notes"] == "Legs felt heavy"
    assert feedback["actualDurationMinutes"] == 120


@pytest.mark.asyncio
async def test_the_coach_reviews_the_whole_session_not_the_half_it_was_handed(
    monkeypatch,
):
    """The defect in #545: 60 + 60 was reviewed as 60 against a 120' plan.

    The matcher returns one representative ride per session on purpose — two
    would mean two coach notes for one session — so the review has to find the
    other half itself.
    """
    date = "2026-08-20"
    plan = [_session(date, "endurance", duration=120)]
    user_id = await _create_user("split-review@example.com", plan)
    await _add_ride(user_id, 9701, date, sport_type="Ride",
                    duration_seconds=60 * 60, start=f"{date}T09:00:00Z")
    await _add_ride(user_id, 9702, date, sport_type="Ride",
                    duration_seconds=60 * 60, start=f"{date}T10:15:00Z")

    rated_days: list[dict] = []

    async def fake_rate(day, profile, **kwargs):
        rated_days.append(day)
        return {"feedback": "Nicely done."}

    monkeypatch.setattr(
        ride_matching.ai_service, "rate_completed_workout", fake_rate
    )

    async def fake_recommend(**kwargs):
        return {"plan_updates": None}

    monkeypatch.setattr(
        ride_matching.ai_service, "recommend_next_session", fake_recommend
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        matched = await ride_matching.apply_ride_plan_matches(
            db, user_id, plan, [9701, 9702]
        )
        # One representative for the one session, both halves matched.
        assert len(matched) == 1
        for ride in matched:
            await ride_matching.review_matched_ride_and_adapt(
                db, user, ride, plan, provider="gemini"
            )
        await db.commit()

    assert len(rated_days) == 1
    assert rated_days[0]["feedback"]["actualDurationMinutes"] == 120


# ---------------------------------------------------------------------------
# Resolving an ambiguous match on a two-a-day (#547)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_manual_resolve_can_name_the_evening_session():
    """Without a slot this could only ever land on the day's first session.

    Which is the wrong half of the problem: ambiguity is most likely exactly
    when a date holds two sessions.
    """
    date = "2026-08-21"
    plan = [
        _session(date, "endurance", slot=0, duration=90),
        _session(date, "intervals", slot=1, duration=60),
    ]
    user_id = await _create_user("resolve-slot@example.com", plan)
    await _add_ride(user_id, 9801, date, sport_type="Ride",
                    duration_seconds=75 * 60, start=f"{date}T07:00:00Z")

    async with TestSessionLocal() as db:
        ride = await ride_matching.resolve_manual_match(
            db, user_id, planned_date=date, strava_activity_id=9801,
            plan=plan, planned_slot=1,
        )
        await db.commit()

    assert ride is not None
    assert ride.matched_plan_slot == 1
    assert ride.matched_plan_snapshot["workoutType"] == "intervals"


@pytest.mark.asyncio
async def test_a_manual_resolve_keeps_the_other_sessions_match():
    """Resolving the evening must not throw away the morning's good match.

    Every other ride of the date used to be unmatched wholesale, which was
    invisible while a resolve could only target slot 0.
    """
    date = "2026-08-22"
    plan = [
        _session(date, "strength", slot=0, duration=45),
        _session(date, "endurance", slot=1, duration=90),
    ]
    user_id = await _create_user("resolve-keeps@example.com", plan)
    await _add_ride(user_id, 9901, date, sport_type="WeightTraining",
                    duration_seconds=45 * 60, start=f"{date}T07:00:00Z")
    await _add_ride(user_id, 9902, date, sport_type="Ride",
                    duration_seconds=90 * 60, start=f"{date}T18:00:00Z")

    async with TestSessionLocal() as db:
        await ride_matching.apply_ride_plan_matches(db, user_id, plan, [9901, 9902])
        await db.commit()

    async with TestSessionLocal() as db:
        await ride_matching.resolve_manual_match(
            db, user_id, planned_date=date, strava_activity_id=9902,
            plan=plan, planned_slot=1,
        )
        await db.commit()

    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_date(db, user_id, date)
    by_id = {r.strava_activity_id: r for r in rides}

    assert by_id[9902].plan_match_status == ride_matching.MATCH_MANUAL
    assert by_id[9902].matched_plan_slot == 1
    # The morning strength session was never in question.
    assert by_id[9901].plan_match_status == ride_matching.MATCH_AUTO
    assert by_id[9901].matched_plan_slot == 0


@pytest.mark.asyncio
async def test_a_manual_resolve_still_displaces_a_rival_for_the_same_session():
    """The behaviour that had to survive the narrowing."""
    date = "2026-08-23"
    plan = [_session(date, "tempo", duration=80)]
    user_id = await _create_user("resolve-displaces@example.com", plan)
    await _add_ride(user_id, 9911, date, sport_type="Ride",
                    duration_seconds=80 * 60, start=f"{date}T07:00:00Z")
    await _add_ride(user_id, 9912, date, sport_type="Ride",
                    duration_seconds=50 * 60, start=f"{date}T18:00:00Z")

    async with TestSessionLocal() as db:
        await ride_matching.apply_ride_plan_matches(db, user_id, plan, [9911, 9912])
        await db.commit()

    async with TestSessionLocal() as db:
        await ride_matching.resolve_manual_match(
            db, user_id, planned_date=date, strava_activity_id=9912, plan=plan,
        )
        await db.commit()

    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_date(db, user_id, date)
    by_id = {r.strava_activity_id: r for r in rides}

    assert by_id[9912].plan_match_status == ride_matching.MATCH_MANUAL
    assert by_id[9911].plan_match_status == ride_matching.MATCH_UNMATCHED


@pytest.mark.asyncio
async def test_without_start_times_the_rides_are_never_summed():
    """Adjacency is a claim about a timeline; there is none here, so no sum."""
    date = "2026-08-19"
    plan = [_session(date, "endurance", duration=120)]
    user_id = await _create_user("no-start-time@example.com", plan)
    await _add_ride(user_id, 9601, date, sport_type="Ride",
                    duration_seconds=60 * 60)
    await _add_ride(user_id, 9602, date, sport_type="Ride",
                    duration_seconds=70 * 60)

    by_id = await _match_and_read(user_id, plan, date, [9601, 9602])

    assert by_id[9602].plan_match_status == ride_matching.MATCH_AUTO
    assert by_id[9601].plan_match_status == ride_matching.MATCH_UNMATCHED
