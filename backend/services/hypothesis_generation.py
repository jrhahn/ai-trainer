"""Backend-owned periodic generation of explicit athlete hypotheses.

Once a week this job reviews each onboarded athlete's accumulated training
history and asks the coach model to form explicit HYPOTHESES — tentative,
testable ideas about cause and effect (e.g. "upper-body strength training
suppresses heart-rate response the following day") that are worth tracking but
not yet trusted enough to act on. Unlike durable insights (see
:mod:`services.insight_generation`), a hypothesis is stored ``proposed`` and
surfaced to the athlete for validation; confirming one promotes it into a memory
fact so it can inform coaching.

Hypotheses are stored through :func:`crud.propose_athlete_hypothesis`, so a
recurring idea accrues evidence and grows in confidence on later runs. The job
runs after the weekly contradiction-detection pass so it works from a freshly
reconciled knowledge base.
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

# Run after the contradiction-detection window (Monday 04:00) so hypotheses are
# formed against a freshly reconciled knowledge base.
HYPOTHESIS_GENERATION_WEEKDAY = 0  # Monday
HYPOTHESIS_GENERATION_HOUR = 5
# The history window fed to the model, and the floor below which there is not
# enough signal to form a testable hypothesis.
HYPOTHESIS_HISTORY_LIMIT = 60
HYPOTHESIS_MIN_ACTIVITIES = 8


async def generate_user_hypotheses(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Form and store explicit hypotheses for one athlete.

    Returns the number of hypotheses proposed (created or strengthened). Returns
    0 when the athlete has too little history or the model finds no idea worth
    testing.
    """
    if not user.is_onboarded:
        return 0

    # A full channel stops before the LLM call, not after it: asking for
    # candidates that will all be declined costs a request and answers nothing
    # (#581).
    policy = uncertainty_lifecycle.HYPOTHESIS_POLICY
    open_count = await crud.count_open_hypotheses(db, user.id)
    if uncertainty_lifecycle.capacity_for(open_count=open_count, policy=policy) <= 0:
        logger.info(
            "Hypothesis generation skipped: channel full user_id=%s open=%s ceiling=%s",
            user.id,
            open_count,
            policy.ceiling,
        )
        return 0

    metrics = await crud.get_ride_metrics_history(
        db, user.id, limit=HYPOTHESIS_HISTORY_LIMIT
    )
    if len(metrics) < HYPOTHESIS_MIN_ACTIVITIES:
        return 0

    metrics_section = ride_metrics_context_section(metrics, timezone_name)
    if not metrics_section.strip():
        return 0

    facts = await crud.list_athlete_memory_facts(db, user.id, now=now)
    existing_facts = [fact.fact for fact in facts]
    hypotheses = await crud.list_athlete_hypotheses(
        db, user.id, include_resolved=True
    )
    existing_hypotheses = [hypothesis.statement for hypothesis in hypotheses]

    async with track_llm_usage(db, user, source="step:hypothesis-generation"):
        candidates = await ai_service.generate_athlete_hypotheses(
            metrics_section,
            existing_facts=existing_facts,
            existing_hypotheses=existing_hypotheses,
            provider=resolve_user_provider(user),
        )

    # One gate, four channels: is reducing this uncertainty worth what reducing
    # it costs on *this* channel? A hypothesis is the cheapest of the four, so
    # the bar is low — but an idea the next few weeks of riding answer for free
    # is not worth prompt budget either (#582).
    weights = await crud.athlete_utility_weights(db, user.id)

    proposed = 0
    for candidate in candidates:
        decision = uncertainty_value.evaluate(
            channel=uncertainty_value.CHANNEL_HYPOTHESIS,
            text=f"{candidate['statement']} {candidate.get('rationale', '')}",
            weights=weights,
        )
        await crud.record_value_decision(
            db, user.id, decision, statement=candidate["statement"], now=now
        )
        if not decision.should_raise:
            continue
        row = await crud.propose_athlete_hypothesis(
            db,
            user.id,
            statement=candidate["statement"],
            category=candidate["category"],
            rationale=candidate["rationale"],
            confidence=candidate["confidence"],
            observed_at=now,
            max_open=policy.ceiling,
        )
        # ``None`` means the channel filled up mid-run. The gate has already
        # recorded why, so there is nothing to report here beyond not counting it.
        if row is None:
            continue
        proposed += 1
    return proposed


# Run for every athlete with a plan by the shared loop (ai-trainer-ops#252). The
# step is looked up when the job runs, so a test can replace it.
JOB = PerUserJob(
    name="athlete-hypothesis-generation",
    label="Athlete hypothesis generation",
    step=lambda db, user, **when: generate_user_hypotheses(db, user, **when),
    counts=('generated',),
    weekday=HYPOTHESIS_GENERATION_WEEKDAY,
    hour=HYPOTHESIS_GENERATION_HOUR,
)
run_hypothesis_generation = JOB.run
