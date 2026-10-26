"""What the coach knows for one chat turn, without the app (ai-trainer-ops#247).

``chat_context.build_coach_context`` is what ask_trainer loaded inline before
it asks the model. These tests call it with a session and a user, no FastAPI.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import timedelta

import pytest

import crud
import models
from domain import motivation_model as mm
from services import ai_service, chat_context, plan_context
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


async def _chat(db, user, *lines: tuple[str, str]) -> None:
    for second, (role, content) in enumerate(lines):
        await crud.create_chat_message(
            db,
            user.id,
            role=role,
            content=content,
            timestamp=f"2026-10-01T08:00:{second:02d}+00:00",
        )


@pytest.mark.asyncio
async def test_the_conversation_is_the_newest_ten_messages(db, user):
    await _chat(
        db,
        user,
        *[("user" if i % 2 == 0 else "assistant", f"message {i}") for i in range(12)],
    )

    conversation = await chat_context.load_conversation(
        db, user.id, "How was my week?", fallback_language="German"
    )

    assert [m["content"] for m in conversation.history] == [
        f"message {i}" for i in range(2, 12)
    ]
    assert conversation.reply_language == "English"


@pytest.mark.asyncio
async def test_a_short_reply_is_answered_in_the_language_the_athlete_wrote(db, user):
    await _chat(
        db,
        user,
        ("user", "Ich bin heute müde und habe keine Lust."),
        # The coach's words are not the athlete's: only the athlete's count.
        (
            "assistant",
            "Then you should ride easy today, and keep it short: it is the best "
            "way to recover, and the plan has room for it this week.",
        ),
    )

    conversation = await chat_context.load_conversation(
        db, user.id, "ok", fallback_language="English"
    )

    assert conversation.reply_language == "German"


@pytest.mark.asyncio
async def test_without_a_language_to_read_the_reply_takes_the_apps(db, user):
    conversation = await chat_context.load_conversation(
        db, user.id, "ok", fallback_language="German"
    )

    assert conversation == chat_context.Conversation(
        history=[], reply_language="German"
    )


@pytest.mark.asyncio
async def test_a_climb_named_earlier_in_the_chat_reaches_the_sections(db, user):
    user.weight_kg = 70
    history = [
        {"role": "user", "content": "Bergzeitfahren Puig Major, 14 km, 850 Hm."},
        {"role": "assistant", "content": "Ein schöner Anstieg."},
    ]

    sections = await chat_context.build_prompt_sections(
        db, user, [], "Wie schnell schaffe ich das?", history, timezone_name="UTC"
    )

    assert "a climb named in this chat" in sections.climb_section


@pytest.mark.asyncio
async def test_a_race_ahead_reaches_the_sections(db, user):
    await _race(db, user, "2099-05-01")

    sections = await chat_context.build_prompt_sections(
        db, user, [], "How is my form?", [], timezone_name="UTC"
    )

    assert [event["date"] for event in sections.race_events] == ["2099-05-01"]
    assert sections.climb_section == ""
    # The plan's priors, held after the reply on the days it changes (#87).
    assert sections.prior_check([]) == []


def test_the_context_reaches_the_model_call_under_its_parameter_names():
    names = [field.name for field in dataclasses.fields(chat_context.CoachContext)]
    context = chat_context.CoachContext(**{name: f"<{name}>" for name in names})

    inputs = context.prompt_inputs()

    renamed = {
        "workout_curiosity_context": "workout_curiosity",
        "rider_identity_context": "rider_identity",
    }
    assert inputs == {renamed.get(name, name): f"<{name}>" for name in names}
    assert set(inputs) <= set(inspect.signature(ai_service.ask_trainer).parameters)


def test_the_sections_reach_the_model_call_under_its_parameter_names():
    writer = plan_context.PlanWriterContext(
        change_history="<history>", coherence="<coherence>", commitments="<agreed>"
    )
    sections = chat_context.PromptSections(
        metrics_section="<metrics>",
        done_minutes={"2026-10-01": 60},
        writer_context=writer,
        prior_check=None,
        coach_saw=[],
        race_events=[{"date": "2099-05-01"}],
        climb_section="<climb>",
        weather_section="<weather>",
    )

    inputs = sections.prompt_inputs()

    assert inputs == {
        "metrics_history_section": "<metrics>",
        "race_events": [{"date": "2099-05-01"}],
        "weather_context_section": "<weather>",
        "plan_changes_section": "<history>",
        "plan_coherence_warnings": "<coherence>",
        "plan_commitment_notes": "<agreed>",
        "done_minutes": {"2026-10-01": 60},
        "prior_check": None,
        "climb_section": "<climb>",
    }
    assert set(inputs) <= set(inspect.signature(ai_service.ask_trainer).parameters)
