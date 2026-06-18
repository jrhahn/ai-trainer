"""Helpers for matching imported rides to scheduled training-plan days."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services import ai_service
from services.analysis import build_ride_analysis, compare_planned_vs_actual

logger = logging.getLogger(__name__)

MATCH_UNMATCHED = "unmatched"
MATCH_AUTO = "auto_matched"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_MANUAL = "manual_matched"


def _is_training_day(day: dict | None) -> bool:
    if not day:
        return False
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "").lower()
    duration = day.get("durationMinutes") or day.get("duration_minutes") or 0
    return workout_type not in {"", "rest"} and int(duration or 0) > 0


def _plan_days_by_date(plan: list[dict] | None) -> dict[str, dict]:
    return {
        str(day["date"]): day
        for day in (plan or [])
        if isinstance(day, dict) and day.get("date") and _is_training_day(day)
    }


def _all_plan_days_by_date(plan: list[dict] | None) -> dict[str, dict]:
    return {
        str(day["date"]): day
        for day in (plan or [])
        if isinstance(day, dict) and day.get("date")
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ride_feedback_from_metric(ride: models.RideMetric) -> dict[str, Any]:
    feedback: dict[str, Any] = {}
    if ride.duration_seconds:
        feedback["actualDurationMinutes"] = max(1, round(ride.duration_seconds / 60))
    if ride.avg_power_w:
        feedback["averagePower"] = ride.avg_power_w
    if ride.user_note:
        feedback["notes"] = ride.user_note
        match = re.search(r"RPE\s+(\d+)\s*/\s*10", ride.user_note, flags=re.IGNORECASE)
        if match:
            # rate_completed_workout's older prompt uses a 1-5 effort scale.
            feedback["perceivedEffort"] = max(1, min(5, round(int(match.group(1)) / 2)))
    if "perceivedEffort" not in feedback:
        feedback["perceivedEffort"] = 3
    return feedback


def _apply_plan_updates(plan: list[dict], plan_updates: list[dict] | None) -> list[dict] | None:
    if not plan_updates:
        return None
    updates_by_date = {u["date"]: u for u in plan_updates if u.get("date")}
    if not updates_by_date:
        return None
    return [
        {**day, **{k: v for k, v in updates_by_date[day["date"]].items() if v is not None}}
        if day.get("date") in updates_by_date
        else day
        for day in plan
    ]


async def apply_ride_plan_matches(
    db: AsyncSession,
    user_id: str,
    plan: list[dict] | None,
    strava_activity_ids: list[int],
) -> list[models.RideMetric]:
    """Apply date-based plan matching for newly inserted rides.

    Returns the rides that were automatically matched and can be reviewed
    without asking the athlete to disambiguate.
    """
    rides = await crud.get_ride_metrics_by_activity_ids(db, user_id, strava_activity_ids)
    if not rides:
        return []

    plan_by_date = _plan_days_by_date(plan)
    display_plan_by_date = _all_plan_days_by_date(plan)
    auto_matched: list[models.RideMetric] = []

    for activity_date in sorted({ride.activity_date for ride in rides}):
        plan_day = plan_by_date.get(activity_date)
        display_plan_day = display_plan_by_date.get(activity_date)
        date_rides = await crud.get_ride_metrics_by_date(db, user_id, activity_date)
        if not date_rides:
            continue

        manual_match = next(
            (
                ride
                for ride in date_rides
                if ride.plan_match_status == MATCH_MANUAL
                and ride.matched_plan_date == activity_date
            ),
            None,
        )
        if manual_match is not None:
            for ride in date_rides:
                if ride.strava_activity_id == manual_match.strava_activity_id:
                    await crud.update_ride_match(
                        db,
                        ride,
                        status=MATCH_MANUAL,
                        matched_plan_date=activity_date if display_plan_day else None,
                        matched_plan_snapshot=display_plan_day,
                        matched_at=ride.matched_at or _utcnow(),
                    )
                else:
                    await crud.update_ride_match(
                        db,
                        ride,
                        status=MATCH_UNMATCHED,
                        matched_plan_date=activity_date if display_plan_day else None,
                        matched_plan_snapshot=display_plan_day,
                    )
            continue

        if plan_day is None:
            for ride in date_rides:
                await crud.update_ride_match(
                    db,
                    ride,
                    status=MATCH_UNMATCHED,
                    matched_plan_date=activity_date if display_plan_day else None,
                    matched_plan_snapshot=display_plan_day,
                )
            continue

        if len(date_rides) == 1:
            ride = date_rides[0]
            label_override = None
            plan_duration_min = plan_day.get("durationMinutes") or plan_day.get("duration_minutes")
            if ride.duration_seconds and plan_duration_min:
                ratio = (ride.duration_seconds / 60) / plan_duration_min
                if ratio > 2.5 or ratio < 0.3:
                    label_override = "Mismatch"
            await crud.update_ride_match(
                db,
                ride,
                status=MATCH_AUTO,
                matched_plan_date=activity_date,
                matched_plan_snapshot=plan_day,
                matched_at=_utcnow(),
                label_override=label_override,
            )
            auto_matched.append(ride)
        else:
            for ride in date_rides:
                await crud.update_ride_match(
                    db,
                    ride,
                    status=MATCH_AMBIGUOUS,
                    matched_plan_date=activity_date,
                    matched_plan_snapshot=plan_day,
                    matched_at=None,
                )

    return auto_matched


async def resolve_manual_match(
    db: AsyncSession,
    user_id: str,
    *,
    planned_date: str,
    strava_activity_id: int,
    plan: list[dict] | None,
) -> models.RideMetric | None:
    """Resolve an ambiguous date by marking one ride as the planned workout."""
    plan_day = _plan_days_by_date(plan).get(planned_date)
    if plan_day is None:
        return None

    rides = await crud.get_ride_metrics_by_date(db, user_id, planned_date)
    selected = next((r for r in rides if r.strava_activity_id == strava_activity_id), None)
    if selected is None:
        return None

    for ride in rides:
        if ride.strava_activity_id == strava_activity_id:
            await crud.update_ride_match(
                db,
                ride,
                status=MATCH_MANUAL,
                matched_plan_date=planned_date,
                matched_plan_snapshot=plan_day,
                matched_at=_utcnow(),
            )
        else:
            await crud.update_ride_match(db, ride, status=MATCH_UNMATCHED)

    return selected


async def review_matched_ride_and_adapt(
    db: AsyncSession,
    user: models.User,
    ride: models.RideMetric,
    plan: list[dict],
    *,
    provider: str,
    streams: dict | None = None,
) -> tuple[str | None, list[dict] | None]:
    """Generate coach feedback for a matched ride and apply next-plan updates.

    The function is intentionally best-effort: failures are logged and surfaced
    as empty results so Strava import/feedback saving does not fail because an
    LLM or stream fetch was unavailable.
    """
    plan_day = ride.matched_plan_snapshot
    if not isinstance(plan_day, dict) and ride.matched_plan_date:
        plan_day = next((day for day in plan if day.get("date") == ride.matched_plan_date), None)
    if not isinstance(plan_day, dict):
        return None, None

    profile = schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True)
    ftp = float(current_ftp) if (current_ftp := (user.current_ftp or 0)) else 0.0
    if ftp <= 0 and user.rider_assessment is not None and user.rider_assessment.estimated_ftp:
        ftp = float(user.rider_assessment.estimated_ftp)

    day_for_rating = dict(plan_day)
    day_for_rating["feedback"] = _ride_feedback_from_metric(ride)
    stream_delta = None
    ride_analysis = None
    if streams and ftp > 0:
        try:
            stream_delta = compare_planned_vs_actual(day_for_rating, streams, ftp=ftp)
            ride_analysis = build_ride_analysis(streams, ftp)
        except Exception:
            logger.warning("Failed to build planned-vs-actual stream context", exc_info=True)

    coach_note: str | None = None
    try:
        result = await ai_service.rate_completed_workout(
            day_for_rating,
            profile,
            provider=provider,
            stream_delta=stream_delta,
            ride_analysis=ride_analysis,
        )
        coach_note = result.get("feedback") or None
        if coach_note:
            await crud.update_ride_metric_notes(
                db,
                user.id,
                ride.strava_activity_id,
                coach_note=coach_note,
            )
            ride.coach_note = coach_note
    except Exception:
        logger.warning("Matched ride coach review failed", exc_info=True)

    plan_updates: list[dict] | None = None
    try:
        rider_assessment = None
        if user.rider_assessment is not None:
            rider_assessment = schemas.RiderAssessmentSchema.model_validate(
                user.rider_assessment, from_attributes=True
            ).model_dump(by_alias=True)
        coach_memory_row = await crud.get_coach_memory(db, user.id)
        coach_memory = coach_memory_row.memory if coach_memory_row is not None else ""
        athlete_context_row = await crud.get_athlete_context(db, user.id)
        athlete_context = (
            schemas.AthleteContextSchema.model_validate(
                athlete_context_row, from_attributes=True
            ).model_dump(by_alias=True)
            if athlete_context_row is not None
            else None
        )
        athlete_memory_fact_rows = await crud.get_prompt_athlete_memory_facts(
            db, user.id
        )
        athlete_memory_facts = [
            schemas.AthleteMemoryFactSchema.model_validate(
                fact, from_attributes=True
            ).model_dump(by_alias=True, mode="json")
            for fact in athlete_memory_fact_rows
        ]
        result = await ai_service.recommend_next_session(
            rides=[ride],
            plan=plan,
            profile=profile,
            provider=provider,
            rider_assessment=rider_assessment,
            coach_memory=coach_memory,
            athlete_context=athlete_context,
            athlete_memory_facts=athlete_memory_facts,
            ctl=float(ride.ctl_after) if ride.ctl_after is not None else None,
            atl=float(ride.atl_after) if ride.atl_after is not None else None,
            tsb=float(ride.tsb_after) if ride.tsb_after is not None else None,
        )
        plan_updates = result.get("plan_updates") or None
        updated_plan = _apply_plan_updates(plan, plan_updates)
        if updated_plan is not None:
            await crud.upsert_training_plan(db, user.id, updated_plan)
    except Exception:
        logger.warning("Matched ride plan adaptation failed", exc_info=True)

    return coach_note, plan_updates
