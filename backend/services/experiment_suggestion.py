"""Backend-owned periodic suggestion of validation experiments.

Once a week this job reviews each athlete's open HYPOTHESES — the tentative,
still-unproven ideas produced by :mod:`services.hypothesis_generation` — and asks
the coach model to propose concrete VALIDATION EXPERIMENTS the athlete can run to
settle them. The principle is: when uncertainty exists, propose an experiment
instead of an assumption. A hypothesis such as "upper-body strength suppresses
next-day heart-rate response" turns into a runnable protocol like "repeat the gym
session and compare heart rate on the following easy ride".

Experiments are stored through :func:`crud.suggest_athlete_experiment`, keyed on
their protocol so re-proposing the same experiment refreshes rather than
duplicates it. The job runs after the weekly hypothesis-generation pass so it
works from a freshly formed set of open questions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import models
from config import settings
from services import ai_service
from services.insight_generation import seconds_until_next_weekly_run
from services.llm import resolve_user_provider
from services.scheduler import ScheduledJob
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# Run after hypothesis generation (Monday 05:00) so experiments target a freshly
# formed set of open questions.
EXPERIMENT_SUGGESTION_WEEKDAY = 0  # Monday
EXPERIMENT_SUGGESTION_HOUR = 6


def _uncertainties_section(hypotheses: list[models.AthleteHypothesis]) -> str:
    """Render open hypotheses as the uncertainty context fed to the model."""
    lines = ["Open questions about this athlete that still need validation:"]
    for hypothesis in hypotheses:
        line = f"- {hypothesis.statement}"
        if hypothesis.rationale:
            line += f" (so far: {hypothesis.rationale})"
        lines.append(line)
    return "\n".join(lines)


async def generate_user_experiments(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
) -> int:
    """Propose and store validation experiments for one athlete.

    Returns the number of experiments suggested (created or refreshed). Returns 0
    when the athlete has no open hypotheses — no uncertainty means nothing to
    validate — or the model designs no useful experiment.
    """
    if not user.is_onboarded:
        return 0

    hypotheses = await crud.list_athlete_hypotheses(db, user.id)
    if not hypotheses:
        return 0

    uncertainties_section = _uncertainties_section(hypotheses)
    hypothesis_by_statement = {h.statement.casefold(): h for h in hypotheses}

    existing = await crud.list_athlete_experiments(db, user.id, include_resolved=True)
    existing_protocols = [experiment.protocol for experiment in existing]

    async with track_llm_usage(db, user, source="experiment-suggestion"):
        candidates = await ai_service.generate_validation_experiments(
            uncertainties_section,
            existing_experiments=existing_protocols,
            provider=resolve_user_provider(user),
        )

    suggested = 0
    for candidate in candidates:
        matched = hypothesis_by_statement.get(candidate["question"].casefold())
        await crud.suggest_athlete_experiment(
            db,
            user.id,
            question=candidate["question"],
            protocol=candidate["protocol"],
            rationale=candidate["rationale"],
            category=candidate["category"],
            hypothesis_id=matched.id if matched is not None else None,
            observed_at=now,
        )
        suggested += 1
    return suggested


@dataclass(slots=True)
class ExperimentSuggestionResult:
    checked: int = 0
    skipped: int = 0
    generated: int = 0
    failed: int = 0


async def run_experiment_suggestion(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> ExperimentSuggestionResult:
    started = datetime.now(timezone.utc)
    result = ExperimentSuggestionResult()
    logger.info("Validation experiment suggestion started")

    async with session_factory() as db:
        users = await crud.get_users_with_training_plans(db)

    for user in users:
        result.checked += 1
        try:
            async with session_factory() as db:
                fresh_user = await crud.get_user_by_id(db, user.id)
                if fresh_user is None:
                    result.skipped += 1
                    continue
                suggested = await generate_user_experiments(
                    db,
                    fresh_user,
                    now=now,
                )
                if suggested:
                    result.generated += suggested
                else:
                    result.skipped += 1
                await db.commit()
        except Exception:
            result.failed += 1
            logger.warning(
                "Validation experiment suggestion failed for user_id=%s",
                user.id,
                exc_info=True,
            )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Validation experiment suggestion finished checked=%s generated=%s "
        "skipped=%s failed=%s duration_ms=%s",
        result.checked,
        result.generated,
        result.skipped,
        result.failed,
        duration_ms,
    )
    return result


def validation_experiment_suggestion_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_experiment_suggestion(session_factory)

    return ScheduledJob(
        name="validation-experiment-suggestion",
        run=_run,
        next_delay=lambda: seconds_until_next_weekly_run(
            timezone_name=settings.app_timezone,
            weekday=EXPERIMENT_SUGGESTION_WEEKDAY,
            hour=EXPERIMENT_SUGGESTION_HOUR,
        ),
    )
