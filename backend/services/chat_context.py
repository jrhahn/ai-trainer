"""What the coach knows about the athlete for one chat turn (ai-trainer-ops#247).

ask_trainer loaded it in some 140 lines before it asked the model: the
assessment, the coach memory and the athlete context, the motivation and
athlete models, the memory facts, open questions, inquiries and hypotheses,
the performance model and its ROI recommendation, and who the athlete is as a
rider. They live here now, in the same order, and nothing here knows HTTP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from domain import roi_recommendation
from services import rider_identity
from services import workout_curiosity
from services.dates import app_today, app_today_iso

logger = logging.getLogger(__name__)


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
