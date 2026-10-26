"""What the coach knows for one chat turn, without the app (ai-trainer-ops#247).

``chat_context.build_coach_context`` is what ask_trainer loaded inline before
it asks the model. These tests call it with a session and a user, no FastAPI.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
import models
from domain import motivation_model as mm
from services import chat_context
from services import workout_curiosity as wc
from services.dates import app_today

# A pattern the rider identity describes the athlete by, stored the way the
# curiosity pass stores it.
CHASE = next(rule for rule in wc.SIGNAL_RULES if rule.name == "chased_someone_down")


async def _remembered(db, user) -> None:
    """An assessment, and one row in every store the memory switch gates."""
    await crud.upsert_rider_assessment(
        db, user.id, estimated_ftp=250, notes="Strong on long climbs."
    )
    await crud.upsert_coach_memory(db, user.id, "Prefers early rides.")
    await crud.upsert_athlete_context(db, user.id, notes="Rides before work.")
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={"primary_objective": "Ride technical trails"},
        source=mm.SOURCE_USER_SET,
    )
    await crud.upsert_athlete_model(db, user.id, ftp_watts=255)
    await crud.observe_athlete_memory_fact(
        db, user.id, fact="Rides to work on Mondays", trusted=True, confidence=0.9
    )
    await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact=CHASE.observation,
        category=wc.MEMORY_CATEGORY,
        trusted=True,
        confidence=0.62,
    )
    await crud.record_athlete_open_question(
        db, user.id, question="Does heat slow you down?"
    )
    await crud.record_athlete_inquiry(
        db, user.id, question="Which day suits a long ride?"
    )
    await crud.upsert_athlete_performance_model(
        db,
        user.id,
        attributes={
            "ftp": {"estimate": 302, "confidence": 0.8},
            # Power held late in long rides: the rider identity reads a style.
            "fatigue_resistance": {"score": "high", "confidence": 0.8},
        },
        likely_limiter="threshold",
        limiters=[
            {
                "limiter": "threshold",
                "confidence": 0.72,
                "evidence": ["FTP low vs MAP"],
                "counter_evidence": [],
            }
        ],
    )
    await crud.propose_athlete_hypothesis(
        db, user.id, statement="Recovers quickly between climbs", confidence=0.6
    )


async def _context(
    db, user, message: str = "How was my week?"
) -> chat_context.CoachContext:
    # The route's user comes with its assessment loaded (auth's eager options);
    # a fixture's row loads it here, so no lazy load runs outside the session.
    await db.refresh(user, ["rider_assessment"])
    return await chat_context.build_coach_context(
        db, user, message, context_workout=None, timezone_name="UTC"
    )


async def _race(db, user, day: str) -> None:
    await crud.create_race_event(
        db, user.id, date=day, start_time=None, distance_km=120.0, elevation_m=1500
    )


@pytest.mark.asyncio
async def test_what_the_coach_remembers_reaches_the_turn(db, user):
    await _remembered(db, user)

    context = await _context(db, user)

    assert context.rider_assessment["notes"] == "Strong on long climbs."
    assert context.coach_memory == "Prefers early rides."
    assert context.athlete_context["notes"] == "Rides before work."
    assert context.motivation_model["primary_objective"] == "Ride technical trails"
    assert context.athlete_model["ftpWatts"] == 255
    assert [fact["fact"] for fact in context.athlete_memory_facts] == [
        "Rides to work on Mondays",
        CHASE.observation,
    ]
    assert [q["question"] for q in context.open_questions] == [
        "Does heat slow you down?"
    ]
    assert [i["question"] for i in context.pending_inquiries] == [
        "Which day suits a long ride?"
    ]
    assert context.performance_model["attributes"]["ftp"]["estimate"] == 302
    assert context.performance_recommendation is not None
    assert [h["statement"] for h in context.hypotheses] == [
        "Recovers quickly between climbs"
    ]
    assert context.rider_identity_context["style"]["reading"]
    assert len(context.rider_identity_context["patterns"]) == 1


@pytest.mark.asyncio
async def test_with_memory_off_the_coach_recalls_nothing(db, user):
    await _remembered(db, user)
    user.memory_updates_enabled = False

    context = await _context(db, user)

    # The assessment is not a memory: it is read either way.
    assert context.rider_assessment["notes"] == "Strong on long climbs."
    assert context.coach_memory == ""
    assert context.athlete_context is None
    assert context.motivation_model is None
    assert context.athlete_model is None
    assert context.athlete_memory_facts == []
    assert context.open_questions == []
    assert context.pending_inquiries == []
    assert context.hypotheses == []
    assert context.performance_model is None
    assert context.performance_recommendation is None
    assert context.rider_identity_context is None


@pytest.mark.asyncio
@pytest.mark.parametrize("memory", [True, False])
async def test_a_session_report_is_read_for_curiosity_only_with_memory_on(
    db, user, memory
):
    # A ride yesterday makes the message a report about a session (#216).
    day = (app_today() - timedelta(days=1)).isoformat()
    db.add(
        models.RideMetric(
            user_id=user.id,
            strava_activity_id=97001,
            activity_source="strava",
            external_activity_id="97001",
            activity_date=day,
            activity_start_datetime=f"{day}T08:00:00",
            activity_name="Ride",
            sport_type="Ride",
            duration_seconds=3600,
            tss=60.0,
        )
    )
    user.memory_updates_enabled = memory

    await _context(db, user, "Ich bin heute müde, können wir es lockerer machen?")

    observations = [
        fact.fact
        for fact in await crud.list_athlete_memory_facts(db, user.id)
        if fact.category == wc.MEMORY_CATEGORY
    ]
    assert bool(observations) is memory


@pytest.mark.asyncio
async def test_only_a_race_still_ahead_reaches_the_recommendation(db, user):
    await _remembered(db, user)
    without = (await _context(db, user)).performance_recommendation

    await _race(db, user, "2020-05-01")
    past_only = (await _context(db, user)).performance_recommendation
    await _race(db, user, "2099-05-01")
    ahead = (await _context(db, user)).performance_recommendation

    assert past_only == without
    assert ahead != without
