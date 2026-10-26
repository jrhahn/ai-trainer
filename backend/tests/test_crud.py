"""Unit tests for the crud data-access layer.

These tests call crud functions directly against the test database, bypassing
the HTTP routers, to verify the data-access logic in isolation.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession, email: str = "test@example.com") -> models.User:
    return await crud.create_user(
        db, email=email, name="Test User", hashed_password="hashed"
    )


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user(db: AsyncSession) -> None:
    user = await _make_user(db)
    assert user.id is not None
    assert user.email == "test@example.com"
    assert user.name == "Test User"


@pytest.mark.asyncio
async def test_get_user_by_id(db: AsyncSession) -> None:
    user = await _make_user(db)
    fetched = await crud.get_user_by_id(db, user.id)
    assert fetched is not None
    assert fetched.id == user.id


@pytest.mark.asyncio
async def test_get_user_by_id_missing(db: AsyncSession) -> None:
    fetched = await crud.get_user_by_id(db, "nonexistent-id")
    assert fetched is None


@pytest.mark.asyncio
async def test_get_user_by_email(db: AsyncSession) -> None:
    await _make_user(db, email="find@example.com")
    fetched = await crud.get_user_by_email(db, "find@example.com")
    assert fetched is not None
    assert fetched.email == "find@example.com"


@pytest.mark.asyncio
async def test_get_user_by_email_simple(db: AsyncSession) -> None:
    await _make_user(db, email="simple@example.com")
    fetched = await crud.get_user_by_email_simple(db, "simple@example.com")
    assert fetched is not None
    assert fetched.email == "simple@example.com"


@pytest.mark.asyncio
async def test_get_user_by_email_simple_missing(db: AsyncSession) -> None:
    fetched = await crud.get_user_by_email_simple(db, "nobody@example.com")
    assert fetched is None


# ---------------------------------------------------------------------------
# TrainingPlan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_training_plan_missing(db: AsyncSession) -> None:
    user = await _make_user(db)
    plan = await crud.get_training_plan(db, user.id)
    assert plan is None


@pytest.mark.asyncio
async def test_upsert_training_plan_creates_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    sample_plan = [{"date": "2026-04-10", "workoutType": "endurance"}]
    plan = await crud.upsert_training_plan(db, user.id, sample_plan)
    assert plan.plan == sample_plan


@pytest.mark.asyncio
async def test_upsert_training_plan_updates_existing(db: AsyncSession) -> None:
    user = await _make_user(db)
    plan_v1 = [{"date": "2026-04-10", "workoutType": "endurance"}]
    plan_v2 = [{"date": "2026-04-11", "workoutType": "tempo"}]
    await crud.upsert_training_plan(db, user.id, plan_v1)
    updated = await crud.upsert_training_plan(db, user.id, plan_v2)
    assert updated.plan == plan_v2


# ---------------------------------------------------------------------------
# WorkoutLog
# ---------------------------------------------------------------------------


_WORKOUT_KWARGS = {
    "actual_duration_minutes": 60,
    "average_power": 220,
    "average_heart_rate": 155,
    "peak_power": 350,
    "perceived_effort": 7,
    "notes": "felt good",
    "completed_at": "2026-04-10T08:00:00Z",
}


@pytest.mark.asyncio
async def test_get_workout_logs_empty(db: AsyncSession) -> None:
    user = await _make_user(db)
    logs = await crud.get_workout_logs(db, user.id)
    assert logs == []


@pytest.mark.asyncio
async def test_upsert_workout_log_creates_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    log = await crud.upsert_workout_log(db, user.id, "2026-04-10", **_WORKOUT_KWARGS)
    assert log.date == "2026-04-10"
    assert log.actual_duration_minutes == 60
    assert log.notes == "felt good"


@pytest.mark.asyncio
async def test_upsert_workout_log_updates_existing(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_workout_log(db, user.id, "2026-04-10", **_WORKOUT_KWARGS)
    updated = await crud.upsert_workout_log(
        db, user.id, "2026-04-10", **{**_WORKOUT_KWARGS, "notes": "updated note"}
    )
    assert updated.notes == "updated note"

    logs = await crud.get_workout_logs(db, user.id)
    assert len(logs) == 1


# ---------------------------------------------------------------------------
# RaceEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_update_delete_race_event(db: AsyncSession) -> None:
    user = await _make_user(db)
    event = await crud.create_race_event(
        db,
        user.id,
        date="2026-06-01",
        start_time="09:00",
        distance_km=120.5,
        elevation_m=1800,
    )
    assert event.id is not None

    events = await crud.get_race_events(db, user.id)
    assert len(events) == 1
    assert events[0].distance_km == 120.5

    updated = await crud.update_race_event(
        db,
        event,
        date="2026-06-02",
        start_time=None,
        distance_km=130,
        elevation_m=2100,
    )
    assert updated.date == "2026-06-02"
    assert updated.start_time is None
    assert updated.elevation_m == 2100

    await crud.delete_race_event(db, updated)
    assert await crud.get_race_events(db, user.id) == []


# ---------------------------------------------------------------------------
# ChatMessage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chat_messages_empty(db: AsyncSession) -> None:
    user = await _make_user(db)
    messages = await crud.get_chat_messages(db, user.id)
    assert messages == []


@pytest.mark.asyncio
async def test_create_chat_message(db: AsyncSession) -> None:
    user = await _make_user(db)
    msg = await crud.create_chat_message(
        db,
        user.id,
        role="user",
        content="Hello",
        timestamp="2026-04-10T10:00:00Z",
        plan_update_count=2,
    )
    assert msg.role == "user"
    assert msg.content == "Hello"
    assert msg.plan_update_count == 2


@pytest.mark.asyncio
async def test_chat_messages_ordered_by_timestamp(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.create_chat_message(
        db, user.id, role="user", content="Second", timestamp="2026-04-10T11:00:00Z"
    )
    await crud.create_chat_message(
        db, user.id, role="assistant", content="First", timestamp="2026-04-10T09:00:00Z"
    )
    messages = await crud.get_chat_messages(db, user.id)
    assert [m.content for m in messages] == ["First", "Second"]


@pytest.mark.asyncio
async def test_chat_messages_with_same_timestamp_keep_insert_order(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.create_chat_message(
        db, user.id, role="user", content="First", timestamp="2026-04-10T10:00:00Z"
    )
    second = await crud.create_chat_message(
        db,
        user.id,
        role="assistant",
        content="Second",
        timestamp="2026-04-10T10:00:00Z",
    )
    messages = await crud.get_chat_messages(db, user.id)
    assert [m.id for m in messages] == [first.id, second.id]


@pytest.mark.asyncio
async def test_delete_chat_messages(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.create_chat_message(
        db, user.id, role="user", content="Bye", timestamp="2026-04-10T10:00:00Z"
    )
    await crud.delete_chat_messages(db, user.id)
    messages = await crud.get_chat_messages(db, user.id)
    assert messages == []


# ---------------------------------------------------------------------------
# Availability constraint lifting (#437)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_availability_constraints_for_dates(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_availability_constraint(
        db,
        user.id,
        constraint_type="required_workout",
        constraint_date="2026-07-24",
        weekday="friday",
        expires_on="2026-07-24",
    )
    await crud.upsert_availability_constraint(
        db,
        user.id,
        constraint_type="no_training",
        constraint_date="2026-07-25",
        weekday="saturday",
        expires_on="2026-07-25",
    )

    lifted = await crud.deactivate_availability_constraints_for_dates(
        db, user.id, ["2026-07-24"]
    )
    assert [c.constraint_date for c in lifted] == ["2026-07-24"]

    active = await crud.list_active_availability_constraints(
        db, user.id, today="2026-07-22"
    )
    # Only the Saturday constraint is still active.
    assert [c.constraint_date for c in active] == ["2026-07-25"]


@pytest.mark.asyncio
async def test_deactivate_availability_constraints_for_dates_empty(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    assert (
        await crud.deactivate_availability_constraints_for_dates(db, user.id, []) == []
    )


@pytest.mark.asyncio
async def test_get_last_flagged_constraint_dates(db: AsyncSession) -> None:
    user = await _make_user(db)
    # An earlier flagged reply, then a later one that supersedes it.
    await crud.create_chat_message(
        db,
        user.id,
        role="assistant",
        content="old",
        timestamp="2026-07-20T10:00:00Z",
        flagged_constraint_dates=["2026-07-20"],
    )
    await crud.create_chat_message(
        db,
        user.id,
        role="assistant",
        content="new",
        timestamp="2026-07-21T10:00:00Z",
        flagged_constraint_dates=["2026-07-24"],
    )
    # A plain reply after it must not clear the last flagged set.
    await crud.create_chat_message(
        db,
        user.id,
        role="assistant",
        content="chit-chat",
        timestamp="2026-07-21T11:00:00Z",
    )
    assert await crud.get_last_flagged_constraint_dates(db, user.id) == ["2026-07-24"]


@pytest.mark.asyncio
async def test_get_last_flagged_constraint_dates_none(db: AsyncSession) -> None:
    user = await _make_user(db)
    assert await crud.get_last_flagged_constraint_dates(db, user.id) == []


# ---------------------------------------------------------------------------
# StravaToken
# ---------------------------------------------------------------------------


_TOKEN_KWARGS = {
    "access_token": "acc123",
    "refresh_token": "ref456",
    "expires_at": 9999999999,
    "athlete_id": 42,
    "athlete_name": "Jane Doe",
}


@pytest.mark.asyncio
async def test_get_strava_token_missing(db: AsyncSession) -> None:
    user = await _make_user(db)
    token = await crud.get_strava_token(db, user.id)
    assert token is None


@pytest.mark.asyncio
async def test_upsert_strava_token_creates_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    token = await crud.upsert_strava_token(db, user.id, **_TOKEN_KWARGS)
    assert token.access_token == "acc123"
    assert token.athlete_name == "Jane Doe"


@pytest.mark.asyncio
async def test_upsert_strava_token_updates_existing(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_strava_token(db, user.id, **_TOKEN_KWARGS)
    updated = await crud.upsert_strava_token(
        db, user.id, **{**_TOKEN_KWARGS, "access_token": "new_acc"}
    )
    assert updated.access_token == "new_acc"


@pytest.mark.asyncio
async def test_delete_strava_token(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_strava_token(db, user.id, **_TOKEN_KWARGS)
    await crud.delete_strava_token(db, user.id)
    token = await crud.get_strava_token(db, user.id)
    assert token is None


@pytest.mark.asyncio
async def test_delete_strava_token_noop_when_missing(db: AsyncSession) -> None:
    user = await _make_user(db)
    # Should not raise even when no token exists
    await crud.delete_strava_token(db, user.id)


# ---------------------------------------------------------------------------
# RiderAssessment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rider_assessment_missing(db: AsyncSession) -> None:
    user = await _make_user(db)
    assessment = await crud.get_rider_assessment(db, user.id)
    assert assessment is None


@pytest.mark.asyncio
async def test_upsert_rider_assessment_creates_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    assessment = await crud.upsert_rider_assessment(
        db,
        user.id,
        estimated_ftp=280,
        rider_type="climber",
        notes="Likes hills",
        hr_zones={"z1": [0, 130]},
        ride_insights="Good aerobic base.",
        last_ride_feedback="Great ride!",
    )
    assert assessment.estimated_ftp == 280
    assert assessment.rider_type == "climber"
    assert assessment.last_ride_feedback == "Great ride!"


@pytest.mark.asyncio
async def test_upsert_rider_assessment_defaults_for_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    assessment = await crud.upsert_rider_assessment(
        db,
        user.id,
        estimated_ftp=None,
    )
    assert assessment.rider_type == "allrounder"
    assert assessment.notes == ""


@pytest.mark.asyncio
async def test_upsert_rider_assessment_updates_existing(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_rider_assessment(
        db,
        user.id,
        estimated_ftp=250,
        rider_type="sprinter",
        notes="Explosive",
    )
    updated = await crud.upsert_rider_assessment(
        db,
        user.id,
        estimated_ftp=270,
        rider_type="allrounder",
        notes="More balanced now",
    )
    assert updated.estimated_ftp == 270
    assert updated.rider_type == "allrounder"
    assert updated.notes == "More balanced now"


@pytest.mark.asyncio
async def test_upsert_rider_assessment_preserves_rider_type_when_none(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    await crud.upsert_rider_assessment(
        db, user.id, estimated_ftp=250, rider_type="sprinter"
    )
    updated = await crud.upsert_rider_assessment(
        db,
        user.id,
        estimated_ftp=260,
        rider_type=None,  # should keep existing "sprinter"
    )
    assert updated.rider_type == "sprinter"


@pytest.mark.asyncio
async def test_upsert_rider_assessment_only_updates_last_ride_feedback_when_truthy(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    await crud.upsert_rider_assessment(
        db,
        user.id,
        estimated_ftp=250,
        last_ride_feedback="Original feedback",
    )
    updated = await crud.upsert_rider_assessment(
        db,
        user.id,
        estimated_ftp=260,
        last_ride_feedback=None,  # should NOT overwrite
    )
    assert updated.last_ride_feedback == "Original feedback"


# ---------------------------------------------------------------------------
# PlanDayHistory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_plan_day_changes_empty_is_noop(db: AsyncSession) -> None:
    user = await _make_user(db, "hist-empty@example.com")

    rows = await crud.record_plan_day_changes(db, user.id, [], "coach_chat")

    assert rows == []
    assert await crud.list_plan_day_history(db, user.id) == []


@pytest.mark.asyncio
async def test_record_plan_day_changes_inserts_rows(db: AsyncSession) -> None:
    user = await _make_user(db, "hist-insert@example.com")
    changes = [
        {
            "date": "2026-05-01",
            "old_day": None,
            "new_day": {"workoutType": "endurance"},
            "applied": True,
        },
        {
            "date": "2026-05-02",
            "old_day": {"workoutType": "vo2max"},
            "new_day": {"workoutType": "recovery"},
            # "applied" omitted → defaults to True
        },
    ]

    rows = await crud.record_plan_day_changes(db, user.id, changes, "ride_review")

    assert len(rows) == 2
    assert all(r.source == "ride_review" for r in rows)
    assert all(r.applied is True for r in rows)  # default applied when omitted
    # Every row from one call shares a batch id so the run can be reassembled.
    assert rows[0].batch_id is not None
    assert len({r.batch_id for r in rows}) == 1

    stored = await crud.list_plan_day_history(db, user.id)
    assert {r.date for r in stored} == {"2026-05-01", "2026-05-02"}
    by_date = {r.date: r for r in stored}
    assert by_date["2026-05-01"].old_day is None
    assert by_date["2026-05-02"].new_day == {"workoutType": "recovery"}


@pytest.mark.asyncio
async def test_record_plan_day_changes_distinct_batch_id_per_call(
    db: AsyncSession,
) -> None:
    user = await _make_user(db, "hist-batch@example.com")
    change = [{"date": "2026-05-01", "old_day": None, "new_day": {"workoutType": "z2"}}]

    first = await crud.record_plan_day_changes(db, user.id, change, "generate")
    second = await crud.record_plan_day_changes(db, user.id, change, "generate")

    # Distinct coach runs get distinct batch ids even with the same source.
    assert first[0].batch_id != second[0].batch_id


@pytest.mark.asyncio
async def test_list_plan_day_history_filters_by_date(db: AsyncSession) -> None:
    user = await _make_user(db, "hist-filter@example.com")
    await crud.record_plan_day_changes(
        db,
        user.id,
        [
            {"date": "2026-05-01", "old_day": None, "new_day": {}, "applied": True},
            {"date": "2026-05-02", "old_day": None, "new_day": {}, "applied": False},
        ],
        "coach_chat",
    )

    only_first = await crud.list_plan_day_history(db, user.id, date="2026-05-01")
    assert len(only_first) == 1
    assert only_first[0].date == "2026-05-01"
