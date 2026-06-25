"""Backend-owned daily maintenance for stale training plans."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import models
import schemas
from config import settings
from services import ai_service
from services.dates import app_today_iso, app_timezone
from services.llm import begin_token_usage_collection, finish_token_usage_collection
from services.plan_constraints import sanitize_plan_for_constraints
from services.prompts import ride_metrics_context_section
from services.scheduler import ScheduledJob
from services.weather_service import training_weather_context_for_user

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


def _provider(user: models.User) -> str:
    stored = user.ai_provider
    if stored == "gemini" and settings.gemini_api_key:
        return "gemini"
    if stored == "openai" and settings.openai_api_key:
        return "openai"
    if settings.gemini_api_key:
        return "gemini"
    if settings.openai_api_key:
        return "openai"
    return "gemini"


def _race_events_for_prompt(events: list[models.RaceEvent]) -> list[dict]:
    return [
        schemas.RaceEventResponse.model_validate(
            event, from_attributes=True
        ).model_dump(by_alias=True)
        for event in events
    ]


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
    if not has_stale_incomplete_days(plan, today):
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

    usage_token = begin_token_usage_collection()
    try:
        updated_plan = await ai_service.adapt_training_plan(
            plan,
            [],
            profile,
            provider=_provider(user),
            rider_assessment=rider_assessment,
            metrics_history_section=metrics_section,
            weather_context_section=weather_section,
            race_events=_race_events_for_prompt(race_events),
            timezone_name=timezone_name,
        )
    finally:
        consumed = finish_token_usage_collection(usage_token)
        if consumed:
            await crud.increment_user_consumed_tokens(db, user, consumed)

    updated_plan = sanitize_plan_for_constraints(updated_plan, constraints)

    # Re-read plan to detect concurrent user edits made during the AI call.
    # For each day, keep the AI's version only if the user hasn't changed it.
    current_row = await crud.get_training_plan(db, user.id)
    current_plan = current_row.plan if current_row is not None else []
    original_by_date = {d["date"]: d for d in plan}
    current_by_date = {d["date"]: d for d in current_plan}

    merged: list[dict] = []
    ai_dates: set[str] = set()
    for day in updated_plan:
        date = day["date"]
        ai_dates.add(date)
        original = original_by_date.get(date)
        current = current_by_date.get(date)
        if original is not None and current is not None and original == current:
            # Unchanged since our read — apply AI update.
            merged.append(day)
        elif current is not None and original != current:
            # User edited this day concurrently — keep their version.
            merged.append(current)
        else:
            # AI added a brand-new date, or user deleted the day — apply AI.
            merged.append(day)

    for date, current_day in current_by_date.items():
        if date not in ai_dates:
            original_day = original_by_date.get(date)
            if original_day is None:
                # User added a day the AI doesn't know about — keep it.
                merged.append(current_day)
            elif current_day != original_day:
                # User edited a day the AI removed/rescheduled — keep user's version.
                merged.append(current_day)
            # else: day unchanged and AI removed it — let AI win.

    merged.sort(key=lambda d: d["date"])

    if merged == current_plan:
        return False
    await crud.upsert_training_plan(db, user.id, merged)
    return True


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
