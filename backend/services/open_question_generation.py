"""Backend-owned periodic maintenance of the athlete's open-questions list (#385).

Once a week this job reviews each onboarded athlete's accumulated training
history and asks the coach model to maintain an explicit OPEN QUESTIONS list —
the specific, coaching-relevant uncertainties the record cannot yet answer
(e.g. "Is FTP underestimated?"), each with the evidence that raises it and what
it still needs to be settled. Unlike a hypothesis (a tentative answer) or a
durable insight (a supported pattern), an open question is the coach openly
tracking what it does not yet know.

Questions are stored through :func:`crud.record_athlete_open_question`, so a
recurring question accrues evidence on later runs and auto-closes once enough
evidence exists. When the model judges the history now answers a tracked
question it is closed with a short resolution. The job runs after the weekly
hypothesis-generation pass so it works from a freshly reconciled knowledge base.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import ai_service, uncertainty_lifecycle, uncertainty_value
from services.llm import resolve_user_provider
from services.prompts import ride_metrics_context_section
from services.token_accounting import track_llm_usage
from services.per_user_jobs import PerUserJob

logger = logging.getLogger(__name__)

# Run after the hypothesis-generation window (Monday 05:00) so questions are
# maintained against a freshly reconciled knowledge base.
OPEN_QUESTION_GENERATION_WEEKDAY = 0  # Monday
OPEN_QUESTION_GENERATION_HOUR = 6
# The history window fed to the model, and the floor below which there is not
# enough signal to raise a meaningful open question.
OPEN_QUESTION_HISTORY_LIMIT = 60
OPEN_QUESTION_MIN_ACTIVITIES = 8


async def generate_user_open_questions(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Maintain the open-questions list for one athlete.

    Returns the number of questions recorded (created, re-evidenced, or resolved).
    Returns 0 when the athlete has too little history or the model finds nothing
    worth asking.
    """
    if not user.is_onboarded:
        return 0

    # Stop before the LLM call when the list is already full (#581).
    policy = uncertainty_lifecycle.OPEN_QUESTION_POLICY
    open_count = await crud.count_open_athlete_open_questions(db, user.id)
    if uncertainty_lifecycle.capacity_for(open_count=open_count, policy=policy) <= 0:
        logger.info(
            "Open-question generation skipped: channel full user_id=%s open=%s "
            "ceiling=%s",
            user.id,
            open_count,
            policy.ceiling,
        )
        return 0

    metrics = await crud.get_ride_metrics_history(
        db, user.id, limit=OPEN_QUESTION_HISTORY_LIMIT
    )
    if len(metrics) < OPEN_QUESTION_MIN_ACTIVITIES:
        return 0

    metrics_section = ride_metrics_context_section(metrics, timezone_name)
    if not metrics_section.strip():
        return 0

    facts = await crud.list_athlete_memory_facts(db, user.id, now=now)
    existing_facts = [fact.fact for fact in facts]
    questions = await crud.list_athlete_open_questions(
        db, user.id, include_resolved=True
    )
    existing_questions = [question.question for question in questions]

    async with track_llm_usage(db, user, source="step:open-question-generation"):
        candidates = await ai_service.generate_open_questions(
            metrics_section,
            existing_facts=existing_facts,
            existing_questions=existing_questions,
            provider=resolve_user_provider(user),
        )

    # An open question costs a line the athlete may read, so it sits above a
    # hypothesis and below anything that asks them to act (#582).
    weights = await crud.athlete_utility_weights(db, user.id)

    recorded = 0
    for candidate in candidates:
        decision = uncertainty_value.evaluate(
            channel=uncertainty_value.CHANNEL_OPEN_QUESTION,
            text=f"{candidate['question']} {candidate.get('needs', '')}",
            weights=weights,
        )
        await crud.record_value_decision(
            db, user.id, decision, statement=candidate["question"], now=now
        )
        if not decision.should_raise:
            continue
        row = await crud.record_athlete_open_question(
            db,
            user.id,
            question=candidate["question"],
            category=candidate["category"],
            evidence=candidate["evidence"],
            needs=candidate["needs"],
            resolved=candidate["resolved"],
            resolution=candidate["resolution"],
            observed_at=now,
            max_open=policy.ceiling,
        )
        # Declined at the ceiling; the gate recorded why.
        if row is None:
            continue
        recorded += 1
    return recorded


# Run for every athlete with a plan by the shared loop (ai-trainer-ops#252). The
# step is looked up when the job runs, so a test can replace it.
JOB = PerUserJob(
    name="athlete-open-question-generation",
    label="Athlete open-question generation",
    step=lambda db, user, **when: generate_user_open_questions(db, user, **when),
    counts=("generated",),
    weekday=OPEN_QUESTION_GENERATION_WEEKDAY,
    hour=OPEN_QUESTION_GENERATION_HOUR,
)
run_open_question_generation = JOB.run
