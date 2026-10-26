"""Generating the whole plan for an athlete (ai-trainer-ops#246).

The generation lived in ``routers/ai.py`` and raised ``HTTPException``. The
nightly rebuild in ``plan_maintenance`` imported it back from the router, the
only service in the backend that imported a router, and caught HTTP errors in a
scheduler job. Here it raises what went wrong in the domain's terms; the router
turns that into a status code, and the nightly run into a log line.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services import (
    ai_service,
    freshness_allocation,
    heart_rate_caution,
    plan_context,
    plan_inputs,
    plan_pipeline,
)
from services.dates import app_today, app_today_iso
from services.llm import resolve_user_provider
from services.prompts import ride_metrics_context_section
from services.token_accounting import track_llm_usage
from services.weather_service import training_weather_context_for_user


class RebuildLimitReached(Exception):
    """Today's full rebuilds are used up (ai-trainer-ops#175)."""


class PlanGenerationFailed(Exception):
    """The model's answer could not be used as a plan, or was empty (#113)."""


def rebuild_day(user: models.User, timezone_name: str | None) -> str:
    """The athlete's own day that rebuilds are counted on (ai-trainer-ops#175).

    The zone the app last reported for the athlete, so a request claiming
    another zone does not open a new day; the request's only when none is known.
    """
    return app_today_iso(timezone_name=user.timezone_name or timezone_name)


async def rebuilds_left(
    db: AsyncSession, user: models.User, timezone_name: str | None
) -> int:
    """How many full rebuilds the athlete has left on their own day."""
    return plan_inputs.rebuilds_left(
        await crud.get_training_plan(db, user.id), rebuild_day(user, timezone_name)
    )


async def mark_rebuilt(
    db: AsyncSession, user: models.User, reasons: list[str], at: str
) -> None:
    """Record that the plan was rebuilt for ``reasons`` (ai-trainer-ops#206)."""
    row = await crud.get_training_plan(db, user.id)
    if row is not None:
        plan_inputs.mark_rebuilt(row, reasons, at)
        await db.flush()


async def generate_plan_for_user(
    db: AsyncSession, user: models.User, timezone_name: str | None
) -> list[dict]:
    """Generate and commit a whole new plan, and return it.

    Raises :class:`RebuildLimitReached` before the model is called when today's
    rebuilds are used up, :class:`PlanGenerationFailed` when the model's answer
    is no plan, and lets ``AIRateLimitError`` through.
    """
    # At most a handful of full rebuilds on the athlete's own day, refused here
    # before the model is called (ai-trainer-ops#175).
    day = rebuild_day(user, timezone_name)
    if plan_inputs.rebuilds_left(await crud.get_training_plan(db, user.id), day) <= 0:
        raise RebuildLimitReached()
    profile = schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True)
    profile = heart_rate_caution.prompt_profile(profile, user.heart_rate_targets_off)
    rider_assessment = None
    if user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    recent_metrics = await crud.get_ride_metrics_history(db, user.id, limit=30)
    metrics_section = ride_metrics_context_section(
        recent_metrics, timezone_name=timezone_name
    )
    weather_section = await training_weather_context_for_user(db, user.id)
    race_events = await plan_context.race_events_for_prompt(db, user.id)
    availability_constraints = await plan_context.active_availability_constraints(
        db, user.id, timezone_name
    )
    profile = plan_context.profile_with_availability_constraints(
        profile, availability_constraints
    )
    # Who this athlete is and what their freshness is for (#602) — the same block
    # the nightly regen builds, so the two triggers plan for the same person.
    athlete_model_section = await freshness_allocation.athlete_model_section_for_user(
        db, user, timezone_name=timezone_name
    )
    # A regeneration builds no writer context, so it reads the phase and the goals
    # itself; the nightly run then plans inside the same phase rather than undoing
    # this one (ai-trainer-ops#64, #65).
    today = app_today(timezone_name=timezone_name)
    phase_section = await plan_context.training_phase_section_for_user(
        db, user.id, today
    )
    # The weeks the new days must fit (ai-trainer-ops#98). No plan to depart from.
    season_section = await plan_context.season_section_for_user(
        db, user.id, None, today
    )
    goal_section = await plan_context.goal_section_for_user(db, user.id, today)
    # No plan to depart from yet, so this is the athlete's stated state only (#18).
    state_section = await plan_context.priors_section_for_user(
        db, user.id, None, today, []
    )
    athlete_model_section = "\n".join(
        part
        for part in (
            athlete_model_section,
            phase_section,
            season_section,
            goal_section,
            state_section,
        )
        if part
    )
    async with track_llm_usage(db, user, source="api:generate-plan"):
        try:
            plan = await ai_service.generate_training_plan(
                profile,
                provider=resolve_user_provider(user),
                rider_assessment=rider_assessment,
                metrics_history_section=metrics_section,
                weather_context_section=weather_section,
                race_events=race_events,
                timezone_name=timezone_name,
                athlete_model_section=athlete_model_section,
                # A new plan is held to the hard priors on every day (ai-trainer-ops#87).
                prior_check=plan_pipeline.full_plan_prior_check(
                    await plan_context.prior_check_for_user(db, user.id, today)
                ),
            )
        except ai_service.AIResponseFormatError as exc:
            raise PlanGenerationFailed() from exc
    # Never commit an empty plan as the athlete's first one (ai-trainer-ops#113).
    # The generator no longer returns one; this is the second lock.
    if not plan:
        raise PlanGenerationFailed()
    existing_plan = await crud.get_training_plan(db, user.id)
    base_plan = existing_plan.plan if existing_plan is not None else []
    commit = await plan_pipeline.commit_plan(
        db,
        user,
        plan,
        base_plan=base_plan,
        source="generate",
        timezone_name=timezone_name,
    )
    # What this plan was built from, so the dashboard can tell when the coach
    # has learned enough to offer a rebuild (ai-trainer-ops#158).
    await plan_inputs.record(db, user)
    row = await crud.get_training_plan(db, user.id)
    if row is not None:
        plan_inputs.count_rebuild(row, day)
        await db.flush()
    return commit.plan
