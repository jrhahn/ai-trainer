"""Logged workouts and manual activities, and the load each one is priced at."""

import logging
from datetime import date as date_type, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import get_db
from services import logged_sessions
from services import reported_effort
from domain import strength_model
from services.dates import app_today_iso

router = APIRouter(prefix="/users/me", tags=["users"])

logger = logging.getLogger(__name__)


@router.get("/workouts")
async def get_workouts(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict[str, dict]:
    logs = await crud.get_workout_logs(db, current_user.id)
    result: dict[str, dict] = {}
    for log in sorted(logs, key=lambda row: (row.date, row.slot or 0)):
        entry = {
            "actualDurationMinutes": log.actual_duration_minutes,
            "averagePower": log.average_power,
            "averageHeartRate": log.average_heart_rate,
            "peakPower": log.peak_power,
            "perceivedEffort": log.perceived_effort,
            "notes": log.notes,
            "completedAt": log.completed_at,
            "slot": log.slot or 0,
            # So an entered activity can be edited in the sport it was entered
            # in (#47); every logged session carries one since #714.
            "sport": log.sport_type,
            # ``None`` travels as null rather than 0: the edit form has to be
            # able to come back empty for a session nobody measured.
            "distanceKm": log.distance_km,
            "normalizedPower": log.normalized_power,
        }
        # Keyed by session (#496): the first session keeps the bare date so every
        # existing client keeps reading exactly what it read before, and only the
        # extra sessions of a two-a-day add a "date#slot" key.
        result[log.date if not log.slot else f"{log.date}#{log.slot}"] = entry
    return result


@router.post("/workouts/{date}")
async def save_workout(
    # A date, as the column (20 characters) and every reader expect: "not-a-date"
    # was stored, and a longer string was a 500 on Postgres (ai-trainer-ops#127).
    date: Annotated[str, Path(pattern=schemas.DATE_PATTERN)],
    body: schemas.WorkoutLogRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    # The pattern lets "2026-99-99" through; the readers parse it (review of #940).
    try:
        date_type.fromisoformat(date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Not a calendar date.",
        ) from exc
    feedback = body.feedback
    slot = schemas.normalize_slot(body.slot)
    # Normalised through the same gate a plan day's sport goes through, so a
    # client-supplied sport lands in the shared vocabulary the load ladder and
    # the ledger read (#710) instead of being stored as whatever was sent.
    sport = (
        schemas.normalise_plan_sport(body.sport)
        if body.sport
        else await _planned_sport_for_session(db, current_user.id, date, slot)
    )

    await crud.upsert_workout_log(
        db,
        current_user.id,
        date,
        slot=slot,
        actual_duration_minutes=feedback.actual_duration_minutes,
        average_power=feedback.average_power,
        average_heart_rate=feedback.average_heart_rate,
        peak_power=feedback.peak_power,
        perceived_effort=feedback.perceived_effort,
        notes=feedback.notes,
        completed_at=feedback.completed_at,
        sport_type=sport,
        # Only what the client sent (ai-trainer-ops#171): a client that does not
        # know these fields must not blank what the athlete entered before.
        **{
            name: getattr(feedback, name)
            for name in ("normalized_power", "distance_km")
            if name in feedback.model_fields_set
        },
    )

    if body.strength_sets is not None:
        parsed_sets = []
        unreadable = 0
        for one_set in body.strength_sets:
            payload = one_set.model_dump(by_alias=False)
            parsed = strength_model.set_from_payload(payload)
            if parsed is not None:
                parsed_sets.append(parsed)
            elif not strength_model.payload_is_blank(payload):
                unreadable += 1

        if unreadable:
            # A blank trailing row is normal and is dropped. A row with content
            # that cannot be read is the athlete having mistyped something, and
            # accepting the session without it would delete their old sets (the
            # write replaces) and answer "ok" for work they believe they saved.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{unreadable} strength set(s) could not be read. Each set "
                    "needs an exercise name, at least one rep and a positive "
                    "weight."
                ),
            )

        await crud.replace_strength_sets(db, current_user.id, date, slot, parsed_sets)

    # Logging effort is new evidence about what the session cost, so the session
    # is re-priced now rather than at the next import (#714) — only ever upwards
    # through the ladder, and only into fatigue and its own sport's fitness,
    # never cycling's (#713). And where nothing imported the session at all,
    # the log is the only record it ever had, so it becomes a session in the
    # load chain in its own right (#745).
    await _price_logged_session(
        db,
        current_user,
        date=date,
        sport=sport,
        perceived_effort=feedback.perceived_effort,
        duration_minutes=feedback.actual_duration_minutes,
    )

    # The log is proof the session happened, so the plan says so too
    # (ai-trainer-ops#111). Without it the nightly job read the day as missed
    # and copied the session forward. In a savepoint: the log is already
    # written and must save even if the plan write fails.
    try:
        async with db.begin_nested():
            await logged_sessions.complete_logged_sessions(
                db, current_user, only={(date, slot)}
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not mark logged session %s/%s completed for user %s",
            date,
            slot,
            current_user.id,
            exc_info=True,
        )

    return {"status": "ok"}


@router.post(
    "/activities",
    response_model=schemas.ManualActivityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_manual_activity(
    body: schemas.ManualActivityRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ManualActivityResponse:
    """Enter a session that no plan day and no recording covers (#47).

    It goes through the same two steps a logged planned session does: the
    reported effort re-prices anything already recorded that day, and the log
    becomes a load row of its own where nothing was recorded (#745). An athlete
    with no device and no intervals.icu therefore still has a fatigue curve.
    """
    try:
        day = date_type.fromisoformat(body.date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date must be a real calendar date",
        ) from exc
    # One day of slack: the server's "today" and the athlete's are a time zone
    # apart, and refusing a session done this evening would be the worse error.
    if day > date_type.fromisoformat(app_today_iso()) + timedelta(days=1):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An activity cannot be in the future.",
        )

    sport = schemas.normalise_plan_sport(body.sport)
    taken = [
        log.slot or 0
        for log in await crud.get_workout_logs(db, current_user.id)
        if log.date == body.date and logged_sessions.is_unplanned_slot(log.slot or 0)
    ]
    slot = max(taken, default=logged_sessions.UNPLANNED_SLOT_BASE - 1) + 1

    await crud.upsert_workout_log(
        db,
        current_user.id,
        body.date,
        slot=slot,
        actual_duration_minutes=body.duration_minutes,
        average_power=None,
        average_heart_rate=body.average_heart_rate,
        peak_power=None,
        perceived_effort=body.perceived_effort,
        notes=body.notes,
        completed_at=f"{body.date}T12:00:00",
        sport_type=sport,
        distance_km=body.distance_km,
        normalized_power=body.normalized_power,
    )
    await _price_logged_session(
        db,
        current_user,
        date=body.date,
        sport=sport,
        perceived_effort=body.perceived_effort,
        duration_minutes=body.duration_minutes,
    )
    return schemas.ManualActivityResponse(date=body.date, slot=slot, sport=sport)


@router.put("/activities/{date}/{slot}", response_model=schemas.ManualActivityResponse)
async def update_manual_activity(
    date: str,
    slot: int,
    body: schemas.ManualActivityUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ManualActivityResponse:
    """Correct an entered activity in place (#47).

    Same slot, so the load row it produced is corrected rather than joined by
    a second one — re-saving a log is how #745 already corrects a figure.
    Only the unplanned range, for the reason deletion is.

    **A field nobody sent is a field nobody corrected.** The upsert replaces the
    whole row, so a body without ``averageHeartRate`` would blank a heart rate
    the entry was created with — and because that figure feeds ``load_for_log``,
    fixing a typo in the note would quietly re-price the session. Omission is
    therefore read as "leave it", while an explicit ``null`` still clears it;
    ``model_fields_set`` is what tells the two apart.
    """
    if not logged_sessions.is_unplanned_slot(slot):
        raise HTTPException(status_code=404, detail="No such entered activity.")
    existing = await crud.get_workout_log_by_date(db, current_user.id, date, slot)
    if existing is None:
        raise HTTPException(status_code=404, detail="No such entered activity.")
    sport = schemas.normalise_plan_sport(body.sport)
    heart_rate = (
        body.average_heart_rate
        if "average_heart_rate" in body.model_fields_set
        else existing.average_heart_rate
    )
    # Same rule as the heart rate, and for the same reason. The dialog sends a
    # distance only for the sports that have one, so an omitted key has to mean
    # "leave it": otherwise correcting a run's note, which sends no distance
    # because the form was not asked about one, would delete the distance the
    # athlete entered. Correcting a run *to* a strength session therefore leaves
    # the stored distance in place — see
    # `test_correcting_a_run_to_strength_keeps_the_distance_for_a_change_back`,
    # which is where that choice is written down.
    distance = (
        body.distance_km
        if "distance_km" in body.model_fields_set
        else existing.distance_km
    )
    normalized_power = (
        body.normalized_power
        if "normalized_power" in body.model_fields_set
        else existing.normalized_power
    )
    await crud.upsert_workout_log(
        db,
        current_user.id,
        date,
        slot=slot,
        actual_duration_minutes=body.duration_minutes,
        average_power=None,
        average_heart_rate=heart_rate,
        peak_power=None,
        distance_km=distance,
        normalized_power=normalized_power,
        perceived_effort=body.perceived_effort,
        notes=body.notes,
        completed_at=f"{date}T12:00:00",
        sport_type=sport,
    )
    await _price_logged_session(
        db,
        current_user,
        date=date,
        sport=sport,
        perceived_effort=body.perceived_effort,
        duration_minutes=body.duration_minutes,
    )
    return schemas.ManualActivityResponse(date=date, slot=slot, sport=sport)


@router.delete("/activities/{date}/{slot}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_manual_activity(
    date: str,
    slot: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    """Remove a session entered by hand, and the load row it produced (#47).

    Only unplanned sessions: a planned session's log is feedback on a plan
    day, and deleting it here would leave the day looking skipped.
    """
    if not logged_sessions.is_unplanned_slot(slot) or not await crud.delete_workout_log(
        db, current_user.id, date, slot
    ):
        raise HTTPException(status_code=404, detail="No such entered activity.")
    try:
        async with db.begin_nested():
            await logged_sessions.reconcile_logged_sessions(db, current_user)
    except Exception:  # noqa: BLE001 - the deletion itself stands
        logger.warning(
            "Could not reconcile logged sessions for user %s",
            current_user.id,
            exc_info=True,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _price_logged_session(
    db: AsyncSession,
    user: models.User,
    *,
    date: str,
    sport: str,
    perceived_effort: int,
    duration_minutes: int,
) -> None:
    """Re-price what was recorded, and give an unrecorded session its own row.

    The two steps every logged session takes (#714, #745), separately guarded:
    they answer different questions, and one failing must not skip the other —
    nor cost the athlete the log they just saved.
    """
    try:
        async with db.begin_nested():
            await reported_effort.apply_reported_effort(
                db,
                user,
                date=date,
                perceived_effort=perceived_effort,
                sport_type=sport,
                duration_minutes=duration_minutes,
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not re-price session %s for user %s from reported effort",
            date,
            user.id,
            exc_info=True,
        )
    try:
        async with db.begin_nested():
            await logged_sessions.reconcile_logged_sessions(db, user)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not reconcile logged sessions for user %s",
            user.id,
            exc_info=True,
        )


async def _planned_sport_for_session(
    db: AsyncSession, user_id: str, date: str, slot: int
) -> str:
    """The sport the plan prescribed for this session, or cycling.

    Read from the plan rather than asked of the client, so every existing client
    keeps working and a logged gym session stops being stored as a bike ride
    (#714). The plan day is the authoritative statement of what the session was
    meant to be (#710); a session that was never planned, or a plan written
    before sports existed, falls back to cycling — which is what the column
    defaulted to for every row written until now.
    """
    try:
        async with db.begin_nested():
            plan_row = await crud.get_training_plan(db, user_id)
    except Exception:  # noqa: BLE001 - a sport is a nicety, saving the log is not
        return schemas.DEFAULT_PLAN_SPORT
    for day in (plan_row.plan if plan_row is not None else None) or []:
        if str((day or {}).get("date") or "") == date and schemas.day_slot(day) == slot:
            return schemas.day_sport(day)
    return schemas.DEFAULT_PLAN_SPORT
