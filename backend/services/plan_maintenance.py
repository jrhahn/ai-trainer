"""Backend-owned daily maintenance for stale training plans."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import models
import schemas
from config import settings
from services import ai_service
from services import coach_summary
from services import freshness_allocation
from services.dates import app_today_iso, app_timezone
from services.llm import resolve_user_provider
from services import plan_context
from services import plan_pipeline
from services.prompts import PLAN_HORIZON_DAYS, ride_metrics_context_section
from services.scheduler import ScheduledJob
from services.weather_service import training_weather_context_for_user
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

DAILY_PLAN_MAINTENANCE_HOUR = 2


@dataclass(slots=True)
class PlanMaintenanceResult:
    checked: int = 0
    skipped: int = 0
    updated: int = 0
    failed: int = 0


def has_stale_incomplete_days(plan: list[dict], today: str) -> bool:
    return any(day.get("date", "") < today and not day.get("completed") for day in plan)


# A session missed last week has already been dealt with, or has not and will
# not be. Re-triggering a full plan review over it every night for the rest of
# the plan's life is what turned this job into 30 batches in 30 days.
MISSED_SESSION_LOOKBACK_DAYS = 3

# Below this many days ahead the rolling window needs refilling. Sits under
# PLAN_HORIZON_DAYS so a plan that is merely a day or two short is left alone
# rather than topped up nightly.
MIN_FUTURE_PLAN_DAYS = 10


def recently_missed_sessions(
    plan: list[dict],
    today: str,
    *,
    lookback_days: int = MISSED_SESSION_LOOKBACK_DAYS,
) -> list[dict]:
    """Sessions in the last ``lookback_days`` that were scheduled and not done.

    Activity import marks a day completed when a matching ride lands (#473), so
    an uncompleted past day means the athlete did not train — something a coach
    would react to. Bounded, because :func:`has_stale_incomplete_days` was true
    for the entire remaining life of the plan once any single day was missed.
    """
    try:
        cutoff = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    except (TypeError, ValueError):
        return []
    return [
        day
        for day in plan
        if isinstance(day, dict)
        and cutoff <= str(day.get("date") or "") < today
        and not day.get("completed")
        and str(day.get("workoutType") or "").strip().lower() not in ("rest", "off", "")
    ]


def future_day_count(plan: list[dict], today: str) -> int:
    """How many days the athlete still has ahead of them, today included."""
    return sum(
        1
        for day in plan
        if isinstance(day, dict) and str(day.get("date") or "") >= today
    )


def maintenance_reasons(
    plan: list[dict],
    today: str,
    *,
    lookback_days: int = MISSED_SESSION_LOOKBACK_DAYS,
    min_future_days: int = MIN_FUTURE_PLAN_DAYS,
) -> list[str]:
    """Why tonight's run is worth making. Empty means: leave the plan alone.

    Deliberately two reasons, both decidable from the plan itself. A new
    availability constraint is *not* one: constraints are enforced at the
    pipeline gate on every write (``sanitize_plan_for_constraints``), so they
    take effect without an LLM run. Weather is not one either — recognising a
    *relevant* change needs a stored forecast baseline that does not exist yet;
    until it does, claiming to react to weather nightly would just be the old
    unconditional rewrite wearing a reason.
    """
    reasons: list[str] = []
    if recently_missed_sessions(plan, today, lookback_days=lookback_days):
        reasons.append("missed_session")
    if future_day_count(plan, today) < min_future_days:
        reasons.append("short_horizon")
    return reasons


def seconds_until_next_daily_run(
    now: datetime | None = None,
    *,
    timezone_name: str | None = None,
    hour: int = DAILY_PLAN_MAINTENANCE_HOUR,
) -> float:
    tz = app_timezone(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(tz)
    next_run = datetime.combine(local_now.date(), time(hour=hour), tzinfo=tz)
    if local_now >= next_run:
        next_run += timedelta(days=1)
    return max(0.0, (next_run - local_now).total_seconds())



def _race_events_for_prompt(events: list[models.RaceEvent]) -> list[dict]:
    return [
        schemas.RaceEventResponse.model_validate(
            event, from_attributes=True
        ).model_dump(by_alias=True)
        for event in events
    ]


def apply_maintenance_updates(
    plan: list[dict],
    plan_updates: list[dict] | None,
    today: str,
    *,
    horizon_days: int = PLAN_HORIZON_DAYS,
) -> list[dict] | None:
    """Fold this run's updates into the stored plan. ``None`` when it changed nothing.

    Two kinds of update, and the distinction matters. A day already in the plan
    is *patched* through the canonical merge, so a day the run did not name comes
    out byte-identical — that is the whole point of #666, and the reason the
    nightly job stopped handing back eleven freshly-worded days every night.

    A date not yet in the plan is an *addition*, which extends the rolling window.
    ``apply_plan_updates`` cannot do that (it iterates the existing plan), and
    until now only the sync-driven full regeneration lengthened the plan at all —
    the trigger removed in #664. Additions are accepted only inside
    ``[today, today + horizon_days]``: a run that wants to append last Tuesday, or
    a day three months out, has lost track of the calendar and is not extending
    anything.
    """
    if not plan_updates:
        return None
    try:
        today_date = date.fromisoformat(today)
    except (TypeError, ValueError):
        return None
    horizon = (today_date + timedelta(days=horizon_days)).isoformat()

    existing_dates = {
        str(day.get("date")) for day in plan if isinstance(day, dict) and day.get("date")
    }
    patches = [u for u in plan_updates if str(u.get("date")) in existing_dates]
    additions = [
        u
        for u in plan_updates
        if str(u.get("date")) not in existing_dates
        and today <= str(u.get("date") or "") <= horizon
    ]
    if not patches and not additions:
        return None

    patched = plan_pipeline.apply_plan_updates(plan, patches)
    proposed = list(patched if patched is not None else plan)
    proposed.extend(additions)
    return proposed


async def maintain_user_training_plan(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> bool:
    plan_row = await crud.get_training_plan(db, user.id)
    plan = plan_row.plan if plan_row is not None else []
    if not plan or not user.is_onboarded:
        return False

    today = app_today_iso(now, timezone_name)
    # No reason, no run — and therefore no message. The old condition was true
    # for the rest of the plan's life once any single day had been missed, which
    # is why this fired on 30 nights out of 30 (#666).
    reasons = maintenance_reasons(plan, today)
    if not reasons:
        return False

    rider_assessment = None
    if user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)

    recent_metrics = await crud.get_ride_metrics_history(db, user.id, limit=30)
    metrics_section = ride_metrics_context_section(recent_metrics, timezone_name)
    weather_section = await training_weather_context_for_user(db, user.id)
    race_events = await crud.get_race_events(db, user.id)

    await crud.deactivate_expired_availability_constraints(db, user.id, today=today)
    constraint_rows = await crud.list_active_availability_constraints(
        db, user.id, today=today
    )
    constraints = [
        schemas.AthleteAvailabilityConstraintSchema.model_validate(
            c, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for c in constraint_rows
    ]
    profile = schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True)
    if constraints:
        profile = {**profile, "availabilityConstraints": constraints}

    # Who this athlete is and what their freshness is for (#602). The nightly run
    # rewrites the week without anyone watching, so it is the trigger that most
    # needs to be planning for the actual athlete rather than a generic one.
    athlete_model_section = await freshness_allocation.athlete_model_section_for_user(
        db, user, timezone_name=timezone_name
    )

    # The same two blocks the chat coach has had since #652/#659. Without them
    # this run plans the week knowing nothing of what the athlete and the coach
    # agreed a few hours earlier, and cannot see a collision it is about to
    # inherit or create (#666).
    writer_context = await plan_context.plan_writer_context(
        db, user.id, plan, now=now, timezone_name=timezone_name
    )

    async with track_llm_usage(db, user, source="step:plan-maintenance"):
        plan_updates = await ai_service.adapt_training_plan(
            plan,
            [],
            profile,
            provider=resolve_user_provider(user),
            rider_assessment=rider_assessment,
            metrics_history_section=metrics_section,
            weather_context_section=weather_section,
            race_events=_race_events_for_prompt(race_events),
            timezone_name=timezone_name,
            athlete_model_section=athlete_model_section,
            plan_change_history=writer_context.change_history,
            plan_coherence_warnings=writer_context.coherence,
        )

    proposed_plan = apply_maintenance_updates(plan, plan_updates, today)
    if proposed_plan is None:
        logger.info(
            "Daily plan maintenance found nothing to change user_id=%s reasons=%s",
            user.id,
            ",".join(reasons),
        )
        return False

    # Hard-constraint enforcement, concurrent-edit protection and persistence
    # are owned by the shared plan pipeline so every trigger behaves identically.
    before_row = await crud.get_training_plan(db, user.id)
    before_plan = before_row.plan if before_row is not None else []
    commit = await plan_pipeline.commit_plan(
        db,
        user,
        proposed_plan,
        base_plan=plan,
        source="nightly_maintenance",
        now=now,
        timezone_name=timezone_name,
    )
    # Explain the run to the athlete in one chat message and store the rationale.
    # Fail-safe: never let narration undo a successful plan update.
    await coach_summary.narrate_plan_changes(
        db,
        user,
        batch_id=commit.batch_id,
        source="nightly_maintenance",
        applied_changes=commit.applied_changes,
        profile=profile,
        rider_assessment=rider_assessment,
        training_load_section=metrics_section,
        # The same forecast the adaptation acted on, so a session moved off a
        # 38 C day is explained as exactly that rather than as unexplained churn.
        weather_context_section=weather_section,
    )
    return commit.plan != before_plan


async def run_daily_plan_maintenance(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> PlanMaintenanceResult:
    started = datetime.now(timezone.utc)
    result = PlanMaintenanceResult()
    logger.info("Daily plan maintenance started")

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
                updated = await maintain_user_training_plan(
                    db,
                    fresh_user,
                    now=now,
                    timezone_name=timezone_name,
                )
                if updated:
                    result.updated += 1
                else:
                    result.skipped += 1
                await db.commit()
        except Exception:
            result.failed += 1
            logger.warning(
                "Daily plan maintenance failed for user_id=%s",
                user.id,
                exc_info=True,
            )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Daily plan maintenance finished checked=%s updated=%s skipped=%s failed=%s duration_ms=%s",
        result.checked,
        result.updated,
        result.skipped,
        result.failed,
        duration_ms,
    )
    return result


def daily_plan_maintenance_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_daily_plan_maintenance(
            session_factory,
            timezone_name=settings.app_timezone,
        )

    return ScheduledJob(
        name="daily-plan-maintenance",
        run=_run,
        next_delay=lambda: seconds_until_next_daily_run(
            timezone_name=settings.app_timezone
        ),
    )
