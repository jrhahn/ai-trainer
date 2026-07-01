"""Unit tests for the crud data-access layer.

These tests call crud functions directly against the test database, bypassing
the HTTP routers, to verify the data-access logic in isolation.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from tests.conftest import TestSessionLocal


# ---------------------------------------------------------------------------
# Session fixture scoped to individual tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Yield a fresh session that is rolled back after each test."""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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


@pytest.mark.asyncio
async def test_get_ride_metrics_by_activity_ids_accepts_external_activity_id(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427463,
        external_activity_id="i157147093",
        activity_date="2026-06-14",
        activity_start_datetime="2026-06-14T08:19:31",
        activity_name="Oberursel (Taunus) Mountain Biking",
        sport_type="MountainBikeRide",
    )

    rides = await crud.get_ride_metrics_by_activity_ids(db, user.id, ["i157147093"])

    assert len(rides) == 1
    assert rides[0].activity_name == "Oberursel (Taunus) Mountain Biking"


@pytest.mark.asyncio
async def test_upsert_ride_metric_uses_source_external_identity_for_imports(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427463,
        activity_source="intervals",
        external_activity_id="i157147093",
        activity_date="2026-06-14",
        activity_start_datetime="2026-06-14T08:19:31",
        activity_name="Original imported activity",
        sport_type="MountainBikeRide",
    )
    second = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427000,
        activity_source="intervals",
        external_activity_id="i157147093",
        activity_date="2026-06-14",
        activity_start_datetime="2026-06-14T08:19:31",
        activity_name="Updated imported activity",
        sport_type="MountainBikeRide",
    )

    history = await crud.get_ride_metrics_history(db, user.id)

    assert second.id == first.id
    assert second.strava_activity_id == 7629419622326427000
    assert len(history) == 1
    assert history[0].activity_name == "Updated imported activity"


@pytest.mark.asyncio
async def test_upsert_ride_metric_reuses_near_duplicate_activity(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427463,
        activity_source="strava",
        external_activity_id="7629419622326427463",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T08:19:31",
        activity_name="Darmstadt Mountain Biking",
        sport_type="Ride",
        duration_seconds=10800,
        summary="first analysis",
    )
    second = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427000,
        activity_source="intervals",
        external_activity_id="i157147093",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T08:19:45",
        activity_name="Darmstadt Mountain Biking",
        sport_type="MountainBikeRide",
        duration_seconds=10812,
        summary="canonical analysis",
    )

    history = await crud.get_ride_metrics_history(db, user.id)

    assert second.id == first.id
    assert second.strava_activity_id == 7629419622326427000
    assert len(history) == 1
    assert history[0].summary == "canonical analysis"


@pytest.mark.asyncio
async def test_get_ride_metrics_history_collapses_stale_near_duplicates(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    db.add_all(
        [
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62001,
                activity_source="strava",
                external_activity_id="62001",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T08:19:31",
                activity_name="Darmstadt Mountain Biking",
                sport_type="Ride",
                duration_seconds=10800,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62002,
                activity_source="intervals",
                external_activity_id="i157147093",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T08:19:45",
                activity_name="Darmstadt Mountain Biking",
                sport_type="MountainBikeRide",
                duration_seconds=10812,
            ),
        ]
    )
    await db.flush()

    history = await crud.get_ride_metrics_history(db, user.id)

    assert len(history) == 1
    assert history[0].strava_activity_id in {62001, 62002}


@pytest.mark.asyncio
async def test_get_ride_metrics_history_collapses_duration_drift_duplicates(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    db.add_all(
        [
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62101,
                activity_source="strava",
                external_activity_id="62101",
                activity_date="2026-06-21",
                activity_start_datetime="2026-06-21T08:03:21+02:00",
                activity_name="Darmstadt Mountain Biking",
                sport_type="MountainBikeRide",
                duration_seconds=96 * 60,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62102,
                activity_source="intervals",
                external_activity_id="i62102",
                activity_date="2026-06-21",
                activity_start_datetime="2026-06-21T08:05:00",
                activity_name="Darmstadt Mountain Biking",
                sport_type="cycling",
                duration_seconds=99 * 60,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62020,
                activity_source="strava",
                external_activity_id="62020",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T09:00:00+02:00",
                activity_name="Darmstadt Road Cycling",
                sport_type="Ride",
                duration_seconds=193 * 60,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62021,
                activity_source="intervals",
                external_activity_id="i62021",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T09:08:00",
                activity_name="Darmstadt Road Cycling",
                sport_type="cycling",
                duration_seconds=205 * 60,
            ),
        ]
    )
    await db.flush()

    history = await crud.get_ride_metrics_history(db, user.id)

    assert len(history) == 2
    assert {ride.activity_name for ride in history} == {
        "Darmstadt Mountain Biking",
        "Darmstadt Road Cycling",
    }


@pytest.mark.asyncio
async def test_get_ride_metrics_history_collapses_contained_ride_duplicate(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    db.add_all(
        [
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62301,
                activity_source="strava",
                external_activity_id="62301",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T09:00:00",
                activity_name="Darmstadt Road Cycling",
                sport_type="cycling",
                duration_seconds=205 * 60,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62302,
                activity_source="intervals",
                external_activity_id="i62302",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T09:04:00",
                activity_name="Darmstadt Mountain Biking",
                sport_type="MountainBikeRide",
                duration_seconds=63 * 60,
            ),
        ]
    )
    await db.flush()

    history = await crud.get_ride_metrics_history(db, user.id)

    assert len(history) == 1
    assert history[0].strava_activity_id == 62301
    assert history[0].duration_seconds == 205 * 60


@pytest.mark.asyncio
async def test_upsert_ride_metric_preserves_longer_contained_duplicate(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=62401,
        activity_source="strava",
        external_activity_id="62401",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T09:00:00",
        activity_name="Darmstadt Road Cycling",
        sport_type="cycling",
        duration_seconds=205 * 60,
    )
    second = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=62402,
        activity_source="intervals",
        external_activity_id="i62402",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T09:04:00",
        activity_name="Darmstadt Mountain Biking",
        sport_type="MountainBikeRide",
        duration_seconds=63 * 60,
    )

    history = await crud.get_ride_metrics_history(db, user.id)

    assert second.id == first.id
    assert second.strava_activity_id == 62401
    assert second.duration_seconds == 205 * 60
    assert len(history) == 1


@pytest.mark.asyncio
async def test_upsert_ride_metric_keeps_separate_same_name_rides_with_different_starts(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=62011,
        activity_source="fit",
        external_activity_id="fit-62011",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T08:00:00+00:00",
        activity_name="FIT Cycling 2026-06-20",
        sport_type="cycling",
        duration_seconds=3600,
    )
    second = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=62012,
        activity_source="fit",
        external_activity_id="fit-62012",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T10:00:00+00:00",
        activity_name="FIT Cycling 2026-06-20",
        sport_type="cycling",
        duration_seconds=3600,
    )

    history = await crud.get_ride_metrics_history(db, user.id)

    assert second.id != first.id
    assert len(history) == 2
    assert {ride.strava_activity_id for ride in history} == {62011, 62012}


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
        db, user.id, role="assistant", content="Second", timestamp="2026-04-10T10:00:00Z"
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
# CoachMemory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_coach_memory_missing(db: AsyncSession) -> None:
    user = await _make_user(db)
    memory = await crud.get_coach_memory(db, user.id)
    assert memory is None


@pytest.mark.asyncio
async def test_upsert_coach_memory_creates_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    memory = await crud.upsert_coach_memory(db, user.id, "Prefers morning rides.")
    assert memory.memory == "Prefers morning rides."


@pytest.mark.asyncio
async def test_upsert_coach_memory_updates_existing(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_coach_memory(db, user.id, "Old memory.")
    updated = await crud.upsert_coach_memory(db, user.id, "New memory.")
    assert updated.memory == "New memory."


# ---------------------------------------------------------------------------
# AthleteContext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_athlete_context_missing(db: AsyncSession) -> None:
    user = await _make_user(db)
    context = await crud.get_athlete_context(db, user.id)
    assert context is None


@pytest.mark.asyncio
async def test_upsert_athlete_context_creates_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    context = await crud.upsert_athlete_context(
        db,
        user.id,
        training_tendency="overtrains",
        rest_response="restless",
        motivation_drivers=["MTB", "race goal"],
        adherence_pattern="adds_extra",
        strengths=["VO2max work"],
        weaknesses=["easy days"],
        preferred_terrain=["singletrack"],
        preferred_session_types=["VO2max", "MTB skills"],
        coaching_risks=["doing too much when fresh"],
        notes="Needs permission to rest.",
    )

    assert context.training_tendency == "overtrains"
    assert context.rest_response == "restless"
    assert context.motivation_drivers == ["MTB", "race goal"]
    assert context.adherence_pattern == "adds_extra"
    assert context.coaching_risks == ["doing too much when fresh"]


@pytest.mark.asyncio
async def test_upsert_athlete_context_updates_existing(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_athlete_context(
        db,
        user.id,
        training_tendency="overtrains",
        motivation_drivers=["race goal"],
    )
    updated = await crud.upsert_athlete_context(
        db,
        user.id,
        training_tendency="balanced",
        rest_response="calm",
        motivation_drivers=["fitness maintenance"],
        adherence_pattern="follows_plan",
    )

    assert updated.training_tendency == "balanced"
    assert updated.rest_response == "calm"
    assert updated.motivation_drivers == ["fitness maintenance"]
    assert updated.adherence_pattern == "follows_plan"


# ---------------------------------------------------------------------------
# AthleteMemoryFact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observe_athlete_memory_fact_repeated_observations_increase_confidence(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Does too much when fresh",
        category="training tendency",
        source_snippet="After a rest day I usually add extra intervals.",
    )
    first_confidence = first.confidence
    second = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="does   too much WHEN fresh",
        category="training tendency",
        source_snippet="I felt fresh so I added a second hard set.",
    )

    assert second.id == first.id
    assert second.category == "training_tendency"
    assert second.observation_count == 2
    assert second.confidence > first_confidence
    assert second.source_snippet == "I felt fresh so I added a second hard set."


@pytest.mark.asyncio
async def test_prompt_athlete_memory_facts_omit_low_confidence_stale_and_rejected(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    included = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Responds well to clear recovery permission",
        category="coaching",
        confidence=0.6,
    )
    low_confidence = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Maybe dislikes gym work",
        category="preference",
        confidence=0.2,
    )
    rejected = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Only trains indoors",
        category="preference",
        confidence=0.8,
    )
    stale = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Often skips Monday sessions",
        category="adherence",
        confidence=0.8,
        observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    confirmed = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Uses MTB races as motivation",
        category="motivation",
        confidence=0.1,
        observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    await crud.update_athlete_memory_fact(
        db, user.id, rejected.id, status="rejected"
    )
    await crud.update_athlete_memory_fact(
        db, user.id, confirmed.id, status="user_confirmed"
    )

    facts = await crud.get_prompt_athlete_memory_facts(
        db,
        user.id,
        now=datetime(2026, 6, 18, tzinfo=timezone.utc),
        stale_after_days=90,
    )
    fact_ids = {fact.id for fact in facts}

    assert included.id in fact_ids
    assert confirmed.id in fact_ids
    assert low_confidence.id not in fact_ids
    assert rejected.id not in fact_ids
    assert stale.id not in fact_ids


@pytest.mark.asyncio
async def test_update_athlete_memory_fact_edits_and_rejects_fact(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Does too much when fresh",
        category="training",
        confidence=0.7,
    )

    updated = await crud.update_athlete_memory_fact(
        db,
        user.id,
        fact.id,
        fact="Adds extra work after rest days",
        category="coaching risk",
        confidence=0.8,
        status="user_confirmed",
        source_snippet="User corrected the wording.",
    )
    assert updated is not None
    assert updated.fact == "Adds extra work after rest days"
    assert updated.category == "coaching_risk"
    assert updated.confidence >= 0.9
    assert updated.status == "user_confirmed"
    assert updated.source_snippet == "User corrected the wording."

    rejected = await crud.update_athlete_memory_fact(
        db, user.id, fact.id, status="rejected"
    )
    assert rejected is not None
    assert rejected.status == "rejected"


@pytest.mark.asyncio
async def test_delete_athlete_memory_fact_removes_fact(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Prefers long endurance rides",
        category="preference",
    )

    deleted = await crud.delete_athlete_memory_fact(db, user.id, fact.id)
    assert deleted is True
    assert await crud.get_athlete_memory_fact(db, user.id, fact.id) is None

    # Deleting an already-removed fact is a no-op that reports failure.
    assert await crud.delete_athlete_memory_fact(db, user.id, fact.id) is False


@pytest.mark.asyncio
async def test_delete_athlete_memory_fact_scoped_to_owner(
    db: AsyncSession,
) -> None:
    owner = await _make_user(db)
    other = await _make_user(db, email="other@example.com")
    fact = await crud.observe_athlete_memory_fact(
        db,
        owner.id,
        fact="Races best in cool weather",
        category="preference",
    )

    # A different user cannot delete someone else's fact.
    assert await crud.delete_athlete_memory_fact(db, other.id, fact.id) is False
    assert await crud.get_athlete_memory_fact(db, owner.id, fact.id) is not None


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
# AthleteMetricSnapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_athlete_metric_snapshot(db: AsyncSession) -> None:
    user = await _make_user(db)
    snapshot = await crud.create_athlete_metric_snapshot(
        db,
        user.id,
        ftp=285,
        ctl=45.3,
        atl=52.1,
        tsb=-6.8,
        source="strava_analysis",
    )
    assert snapshot.ftp == 285
    assert snapshot.ctl == 45.3
    assert snapshot.atl == 52.1
    assert snapshot.tsb == -6.8
    assert snapshot.source == "strava_analysis"
    assert snapshot.user_id == user.id
    assert snapshot.id is not None
    assert snapshot.recorded_at is not None


@pytest.mark.asyncio
async def test_create_athlete_metric_snapshot_nulls(db: AsyncSession) -> None:
    user = await _make_user(db)
    snapshot = await crud.create_athlete_metric_snapshot(
        db,
        user.id,
        ftp=None,
    )
    assert snapshot.ftp is None
    assert snapshot.ctl is None
    assert snapshot.atl is None
    assert snapshot.tsb is None


@pytest.mark.asyncio
async def test_get_athlete_metric_history_empty(db: AsyncSession) -> None:
    user = await _make_user(db)
    history = await crud.get_athlete_metric_history(db, user.id)
    assert history == []


@pytest.mark.asyncio
async def test_get_athlete_metric_history_ordered_ascending(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.create_athlete_metric_snapshot(db, user.id, ftp=250)
    await crud.create_athlete_metric_snapshot(db, user.id, ftp=265)
    await crud.create_athlete_metric_snapshot(db, user.id, ftp=280)

    history = await crud.get_athlete_metric_history(db, user.id)
    assert len(history) == 3
    # Should be ordered oldest → newest (recorded_at ascending)
    ftps = [s.ftp for s in history]
    assert ftps == [250, 265, 280]


@pytest.mark.asyncio
async def test_get_athlete_metric_history_limit(db: AsyncSession) -> None:
    user = await _make_user(db)
    for ftp in range(100, 200, 5):  # 20 snapshots
        await crud.create_athlete_metric_snapshot(db, user.id, ftp=ftp)

    history = await crud.get_athlete_metric_history(db, user.id, limit=5)
    assert len(history) == 5
    ftps = [s.ftp for s in history]
    assert ftps == [175, 180, 185, 190, 195]


# ---------------------------------------------------------------------------
# get_unreviewed_ride_metrics / mark_rides_as_reviewed
# ---------------------------------------------------------------------------


async def _make_ride(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
    activity_date: str,
) -> models.RideMetric:
    return await crud.upsert_ride_metric(
        db,
        user_id,
        strava_activity_id=strava_activity_id,
        activity_date=activity_date,
        sport_type="cycling",
        duration_seconds=3600,
        tss=80.0,
    )


@pytest.mark.asyncio
async def test_get_unreviewed_ride_metrics_empty(db: AsyncSession) -> None:
    user = await _make_user(db)
    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert unreviewed == []


@pytest.mark.asyncio
async def test_get_unreviewed_ride_metrics_returns_all_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 1001, "2026-05-01")
    await _make_ride(db, user.id, 1002, "2026-05-02")
    await _make_ride(db, user.id, 1003, "2026-05-03")

    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert len(unreviewed) == 3
    # Should be ordered oldest first
    dates = [m.activity_date for m in unreviewed]
    assert dates == ["2026-05-01", "2026-05-02", "2026-05-03"]


@pytest.mark.asyncio
async def test_get_unreviewed_ride_metrics_excludes_reviewed(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 2001, "2026-05-01")
    await _make_ride(db, user.id, 2002, "2026-05-02")

    # Mark first ride as reviewed
    await crud.mark_rides_as_reviewed(db, user.id, [2001])

    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert len(unreviewed) == 1
    assert unreviewed[0].strava_activity_id == 2002


@pytest.mark.asyncio
async def test_mark_rides_as_reviewed_sets_timestamp(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 3001, "2026-05-01")

    count = await crud.mark_rides_as_reviewed(db, user.id, [3001])
    assert count == 1

    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert unreviewed == []


@pytest.mark.asyncio
async def test_mark_rides_as_reviewed_empty_list(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 4001, "2026-05-01")

    count = await crud.mark_rides_as_reviewed(db, user.id, [])
    assert count == 0

    # Ride should still be unreviewed
    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert len(unreviewed) == 1


@pytest.mark.asyncio
async def test_mark_rides_as_reviewed_only_affects_given_ids(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 5001, "2026-05-01")
    await _make_ride(db, user.id, 5002, "2026-05-02")
    await _make_ride(db, user.id, 5003, "2026-05-03")

    count = await crud.mark_rides_as_reviewed(db, user.id, [5001, 5003])
    assert count == 2

    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert len(unreviewed) == 1
    assert unreviewed[0].strava_activity_id == 5002


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

    stored = await crud.list_plan_day_history(db, user.id)
    assert {r.date for r in stored} == {"2026-05-01", "2026-05-02"}
    by_date = {r.date: r for r in stored}
    assert by_date["2026-05-01"].old_day is None
    assert by_date["2026-05-02"].new_day == {"workoutType": "recovery"}


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
