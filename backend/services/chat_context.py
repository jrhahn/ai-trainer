"""What the coach knows about the athlete for one chat turn (ai-trainer-ops#247).

ask_trainer loaded it in some 140 lines before it asked the model: the
assessment, the coach memory and the athlete context, the motivation and
athlete models, the memory facts, open questions, inquiries and hypotheses,
the performance model and its ROI recommendation, and who the athlete is as a
rider. The conversation and the reply's language, and the sections about the
rides, the plan, races, climbs and weather, followed in the route. They all
live here now, in the same order, and nothing here knows HTTP.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from config import settings
from domain import roi_recommendation
from services import climb_power, plan_context, plan_pipeline
from services import rider_identity
from services import workout_curiosity
from services.ai_service import MAX_CONVERSATION_HISTORY
from services.athlete_language import athlete_language
from services.dates import app_today, app_today_iso
from services.prompts import ride_metrics_context_section
from services.ride_matching import done_minutes_by_session
from services.weather_service import training_weather_context_for_user

logger = logging.getLogger(__name__)

# How many of the newest chat messages a coach turn loads (#284): ten become
# the history, and the note language reads the last five of the athlete's.
CHAT_CONTEXT_MESSAGES = 5 * MAX_CONVERSATION_HISTORY

# How far back the coach re-reads its own past ride notes (#513).  The history
# window stays at 30 rides so trend questions keep their numbers; only the prose
# tail is cut, and the coach's own notes were 45 % of that section in production
# (2,278 of 5,063 tokens, re-read on every message).  Seven rides is about a week
# for this athlete — the 30 rides span 33 days — so "how did last week go" is
# still answered from full notes, while the coach stops quoting itself from six
# weeks ago.
RIDE_NOTE_PROSE_WINDOW = 7


@dataclass
class CoachContext:
    """What the coach knows about the athlete for this turn.

    Everything durable is gated on the athlete's memory switch: with memory off,
    the coach recalls none of it. The rider assessment is read either way.
    """

    rider_assessment: dict | None
    coach_memory: str
    athlete_context: dict | None
    motivation_model: dict | None
    athlete_model: dict | None
    workout_curiosity_context: dict | None
    athlete_memory_facts: list[dict]
    open_questions: list[dict]
    pending_inquiries: list[dict]
    performance_model: dict | None
    performance_recommendation: dict | None
    hypotheses: list[dict]
    rider_identity_context: dict | None

    def prompt_inputs(self) -> dict:
        """These, by the names the model call takes them (ai-trainer-ops#247)."""
        return {
            "rider_assessment": self.rider_assessment,
            "coach_memory": self.coach_memory,
            "athlete_context": self.athlete_context,
            "athlete_memory_facts": self.athlete_memory_facts,
            "athlete_model": self.athlete_model,
            "motivation_model": self.motivation_model,
            "open_questions": self.open_questions,
            "pending_inquiries": self.pending_inquiries,
            "performance_model": self.performance_model,
            "performance_recommendation": self.performance_recommendation,
            "hypotheses": self.hypotheses,
            "workout_curiosity": self.workout_curiosity_context,
            "rider_identity": self.rider_identity_context,
        }


async def build_coach_context(
    db: AsyncSession,
    current_user: models.User,
    message: str,
    *,
    context_workout: object | None,
    timezone_name: str | None,
) -> CoachContext:
    """The memory-gated loads ask_trainer ran before the model call, in the same
    order (ai-trainer-ops#247): the assessment, the coach memory, the athlete
    context, the motivation and athlete models, what is worth being curious
    about in ``message``, the memory facts, open questions and pending
    inquiries, the performance model with its ROI recommendation and the
    hypotheses, and who the athlete is as a rider."""
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    memory_enabled = current_user.memory_updates_enabled
    coach_memory_row = await crud.get_coach_memory(db, current_user.id)
    coach_memory = (
        (coach_memory_row.memory if coach_memory_row is not None else "")
        if memory_enabled
        else ""
    )
    athlete_context_row = await crud.get_athlete_context(db, current_user.id)
    athlete_context = (
        schemas.AthleteContextSchema.model_validate(
            athlete_context_row, from_attributes=True
        ).model_dump(by_alias=True)
        if (athlete_context_row is not None and memory_enabled)
        else None
    )
    # What the athlete is training for (#562). Gated on the same memory switch
    # as the rest of the durable profile: an athlete who turned memory off does
    # not want their objective recalled either.
    motivation_row = await crud.get_athlete_motivation_model(db, current_user.id)
    motivation_model = (
        crud.motivation_model_as_dict(motivation_row)
        if (motivation_row is not None and memory_enabled)
        else None
    )
    athlete_model_row = await crud.get_athlete_model(db, current_user.id)
    athlete_model = (
        schemas.AthleteModelSchema.model_validate(
            athlete_model_row, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        if (athlete_model_row is not None and memory_enabled)
        else None
    )
    # What is worth being curious about in what the athlete just said (#593).
    # Deterministic and best-effort, like the captures above it, and gated on
    # memory for the same reason the motivation model is: it both reads and
    # writes durable beliefs about the athlete.
    workout_curiosity_context: dict | None = None
    if memory_enabled:
        try:
            async with db.begin_nested():
                # Only a message about a session (ai-trainer-ops#216).
                if await workout_curiosity.reports_a_session(
                    db,
                    current_user.id,
                    today=app_today(timezone_name=timezone_name),
                    context_workout=context_workout,
                ):
                    workout_curiosity_context = (
                        await workout_curiosity.curiosity_for_message(
                            db,
                            current_user.id,
                            message,
                            weights=(motivation_model or {}).get("weights"),
                        )
                    )
        except Exception:
            logger.warning("Workout-curiosity pass failed", exc_info=True)
    athlete_memory_fact_rows = (
        await crud.get_prompt_athlete_memory_facts(db, current_user.id)
        if memory_enabled
        else []
    )
    athlete_memory_facts = [
        schemas.AthleteMemoryFactSchema.model_validate(
            fact, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for fact in athlete_memory_fact_rows
    ]
    # Capped, not the full list (#512) — see get_prompt_athlete_open_questions.
    open_question_rows = (
        await crud.get_prompt_athlete_open_questions(db, current_user.id)
        if memory_enabled
        else []
    )
    open_questions = [
        schemas.AthleteOpenQuestionSchema.model_validate(
            question, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for question in open_question_rows
    ]
    # Questions already pinned to the athlete (#506) — passed so the coach does
    # not ask them a second time in prose while the pin is still waiting.
    pending_inquiry_rows = (
        await crud.list_athlete_inquiries(db, current_user.id) if memory_enabled else []
    )
    pending_inquiries = [
        schemas.AthleteInquirySchema.model_validate(
            inquiry, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for inquiry in pending_inquiry_rows
    ]

    # Deterministic Athlete Performance Model (#476/#477), its ROI recommendation
    # (#478) and the active testable hypotheses (#479) — the structured substrate
    # the coach drills into to explain WHY on demand and to surface a higher-return
    # emphasis proactively (#480).
    performance_model: dict | None = None
    performance_recommendation: dict | None = None
    hypotheses: list[dict] = []
    if memory_enabled:
        perf_model_row = await crud.get_athlete_performance_model(db, current_user.id)
        if perf_model_row is not None:
            performance_model = schemas.AthletePerformanceModelSchema.model_validate(
                perf_model_row, from_attributes=True
            ).model_dump(by_alias=False, mode="json")
            performance_recommendation = roi_recommendation.recommend_training_roi(
                perf_model_row.attributes,
                perf_model_row.limiters,
                motivation=motivation_model,
                upcoming_races=await _upcoming_race_count(
                    db, current_user.id, timezone_name
                ),
            )
        # Capped, not the full list (#512) — see get_prompt_athlete_hypotheses.
        hypothesis_rows = await crud.get_prompt_athlete_hypotheses(db, current_user.id)
        hypotheses = [
            schemas.AthleteHypothesisSchema.model_validate(
                hypothesis, from_attributes=True
            ).model_dump(by_alias=False, mode="json")
            for hypothesis in hypothesis_rows
        ]
    # Who this athlete is as a rider (#597) — read after the performance model,
    # since the style half comes off it. Gated on memory like everything durable,
    # and best-effort: it explains a recommendation, it never makes one.
    rider_identity_context: dict | None = None
    if memory_enabled:
        try:
            async with db.begin_nested():
                rider_identity_context = await rider_identity.identity_for_prompt(
                    db, current_user.id, performance_model=performance_model
                )
        except Exception:
            logger.warning("Rider-identity read failed", exc_info=True)
    return CoachContext(
        rider_assessment=rider_assessment,
        coach_memory=coach_memory,
        athlete_context=athlete_context,
        motivation_model=motivation_model,
        athlete_model=athlete_model,
        workout_curiosity_context=workout_curiosity_context,
        athlete_memory_facts=athlete_memory_facts,
        open_questions=open_questions,
        pending_inquiries=pending_inquiries,
        performance_model=performance_model,
        performance_recommendation=performance_recommendation,
        hypotheses=hypotheses,
        rider_identity_context=rider_identity_context,
    )


@dataclass
class Conversation:
    """The turn's history, and the language the reply is written in."""

    history: list[dict]
    reply_language: str


@dataclass
class PromptSections:
    """What the coach is shown about the rides, the plan, races, climbs and
    weather. ``coach_saw`` is the same for the athlete, under the reply."""

    metrics_section: str
    done_minutes: dict
    writer_context: plan_context.PlanWriterContext
    prior_check: Callable[[list[dict] | None], list] | None
    coach_saw: list[dict]
    race_events: list[dict]
    climb_section: str
    weather_section: str

    def prompt_inputs(self) -> dict:
        """These, by the names the model call takes them (ai-trainer-ops#247)."""
        return {
            "metrics_history_section": self.metrics_section,
            "race_events": self.race_events,
            "weather_context_section": self.weather_section,
            "plan_changes_section": self.writer_context.change_history,
            "plan_coherence_warnings": self.writer_context.coherence,
            "plan_commitment_notes": self.writer_context.commitments,
            "done_minutes": self.done_minutes,
            "prior_check": self.prior_check,
            "climb_section": self.climb_section,
        }


async def load_conversation(
    db: AsyncSession, user_id: str, message: str, *, fallback_language: str
) -> Conversation:
    """The newest messages and the reply's language, as ask_trainer read them
    (ai-trainer-ops#247). ``fallback_language`` is the app's interface language,
    for a message that does not say which one it is in."""
    # The newest messages only (#284): the history below takes the last ten.
    chat_messages = await crud.get_chat_messages(
        db, user_id, limit=CHAT_CONTEXT_MESSAGES
    )
    conversation_history = [
        {"role": msg.role, "content": msg.content}
        for msg in chat_messages[-MAX_CONVERSATION_HISTORY:]
    ]
    # The reply's language, decided once: the coach is told it and the app's
    # own notes are written in it. Until #262 the router read the last five of
    # the athlete's messages and the service the last ten.
    reply_language = (
        athlete_language(
            message,
            [m["content"] for m in conversation_history if m["role"] == "user"],
        )
        or fallback_language
    )
    return Conversation(history=conversation_history, reply_language=reply_language)


async def build_prompt_sections(
    db: AsyncSession,
    current_user: models.User,
    plan: list[dict],
    message: str,
    history: list[dict],
    *,
    timezone_name: str | None,
) -> PromptSections:
    """The sections ask_trainer built for the model call, in the same order
    (ai-trainer-ops#247): the rides, the plan's history and collisions, the
    races, the climbs the athlete named and the weather."""
    recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=30)
    # Keep the full 30-ride window — trend questions are answered from the
    # metrics lines — but stop replaying the coach's own older notes back
    # to it on every message (#513).
    metrics_section = ride_metrics_context_section(
        recent_metrics,
        timezone_name=timezone_name,
        prose_window=RIDE_NOTE_PROSE_WINDOW,
    )
    # How the plan got to its current state (#652), and which adjacent
    # days already collide (#659). Built by the shared helper so the
    # nightly run and auto-adapt see exactly what the chat coach sees —
    # they used to see neither, which is #666.
    writer_context = await plan_context.plan_writer_context(
        db, current_user.id, plan, timezone_name=timezone_name
    )
    # The same sections, for the athlete to read under the reply (#17).
    coach_saw = plan_context.coach_saw(
        writer_context.sections, settings.git_sha or "develop"
    )
    race_events = await plan_context.race_events_for_prompt(db, current_user.id)
    # Climb watts from the app, which the coach quotes (ai-trainer-ops#188).
    climb_section = climb_power.prompt_section(
        climb_power.known_climbs(
            [
                message,
                *[m["content"] for m in reversed(history) if m["role"] == "user"],
            ],
            current_user.race_date,
            current_user.race_description,
            race_events,
            app_today_iso(timezone_name=timezone_name),
        ),
        current_user.weight_kg,
        current_user.current_ftp,
    )
    # The upcoming outlook near the athlete's training location plus their
    # learned tolerances — the coach chat is where "should I ride tomorrow?"
    # actually gets asked (#495). Cached per location, so this is cheap.
    weather_section = await training_weather_context_for_user(db, current_user.id)
    return PromptSections(
        metrics_section=metrics_section,
        # What a done day's rides took, beside what was planned.
        done_minutes=done_minutes_by_session(recent_metrics),
        writer_context=writer_context,
        # The hard priors, the stated hours and the states are held after
        # the reply is written, on the days its planUpdates change
        # (ai-trainer-ops#87, #103 Q37), and the race and state priors on
        # any day of the coming week (ai-trainer-ops#103 Q33, Q37).
        prior_check=plan_pipeline.update_prior_check(
            writer_context.prior_check,
            plan,
            today=app_today(timezone_name=timezone_name),
        ),
        coach_saw=coach_saw,
        race_events=race_events,
        climb_section=climb_section,
        weather_section=weather_section,
    )


async def _upcoming_race_count(
    db: AsyncSession, user_id: str, timezone_name: str | None
) -> int:
    """How many races the athlete still has ahead of them (#564).

    Race specificity is worth nothing with an empty calendar, so the utility
    scorer needs this to stop weighting race-ready options for an athlete who
    has stopped racing.
    """
    today = app_today_iso(timezone_name=timezone_name)
    events = await crud.get_race_events(db, user_id)
    return sum(1 for event in events if (event.date or "") >= today)
