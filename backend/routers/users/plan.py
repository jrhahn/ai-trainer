"""The plan: reading and saving it, its history, and the day board.

Also the plan inputs the athlete can remove: an availability constraint, a
state, a day preference.
"""

import inspect
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import get_db
from services import freshness_allocation
from domain import day_preference
from services import plan_translations
from domain import untrusted_text
from services.athlete_language import interface_language
from services import plan_pipeline
from services.dates import app_today, request_timezone

router = APIRouter(prefix="/users/me", tags=["users"])


class PlanDayHistoryEntry(schemas.CamelModel):
    """One athlete-visible plan-day change (see #343 / #357).

    ``source`` is the raw trigger key (``coach_chat``, ``ride_review``, …); the
    frontend maps it to a friendly label. ``applied=False`` marks an automated
    change that a user pin or completed day blocked ("attempted but kept").
    """

    id: str
    date: str
    source: str
    applied: bool
    recorded_at: datetime
    # Shared by all rows from one coach run so the frontend can collapse a run
    # into a single timeline card; null for rows written before #435.
    batch_id: str | None
    old_day: Any | None
    new_day: Any | None
    # One-line coach rationale for this day's change (#439); null when the run was
    # not narrated (user edits, initial generation) or predates the feature.
    reason: str | None = None
    # True when this run was narrated as a coach chat message (#439). The frontend
    # suppresses the redundant Coach-Timeline card for narrated runs.
    narrated: bool = False


class PlanDayHistoryResponse(schemas.CamelModel):
    entries: list[PlanDayHistoryEntry]
    total: int


class PlanDayHistoryDateCount(schemas.CamelModel):
    date: str
    count: int


class PlanDayHistoryStatsResponse(schemas.CamelModel):
    by_source: dict[str, int]
    applied_count: int
    blocked_count: int
    most_changed_dates: list[PlanDayHistoryDateCount]
    total: int


@router.get("/plan", response_model=schemas.PlanResponse)
async def get_plan(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanResponse:
    """The plan, and for an interface it is translated into, the translations
    already written, so that the plan comes up in the athlete's language
    (ai-trainer-ops#271). No model call: a text still untranslated stays
    English, and the page asks ``POST /ai/plan-translations`` for it.
    """
    plan = await crud.get_training_plan(db, current_user.id)
    code = plan_translations.LANGUAGE_CODES.get(interface_language(request) or "")
    translations: dict[str, str] = {}
    if code is not None:
        translations, _ = await plan_translations.current_translations(
            db,
            current_user.id,
            code,
            app_today(timezone_name=request_timezone(request)),
        )
    # A stored day may still carry the data markers a plan writer copied into
    # it; the gate drops them on the next write, and this shows them gone now.
    return schemas.PlanResponse(
        plan=untrusted_text.unmark(plan.plan) if plan is not None else [],
        translations=translations,
    )


def _stored_feedback(raw: object) -> schemas.WorkoutFeedbackSchema | None:
    """A stored day's workout feedback as the model, or None when unreadable.

    Carried over from the stored plan rather than taken from the client. A
    value that no longer validates is dropped rather than failing the save.
    """
    if not raw:
        return None
    try:
        return schemas.WorkoutFeedbackSchema.model_validate(raw)
    except ValidationError:
        return None


@router.put("/plan", response_model=schemas.PlanResponse)
async def save_plan(
    body: schemas.PlanRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanResponse:
    # Route the manual edit through the shared pipeline so hard availability
    # constraints are enforced and concurrent edits are protected, instead of
    # blindly persisting whatever the client sent. Validate each day as a
    # canonical PlanDay and take the server-authoritative fields from the stored
    # plan — pinning (``source``), completion state (``completed``) and workout
    # ``feedback`` are owned by the server, so a client must not be able to forge
    # them.
    #
    # Nor to clear them (ai-trainer-ops#111). This used to set both to None on
    # every day, and the web app saves the whole plan after every hand-logged
    # session. So each log un-completed every day a synced ride had completed,
    # and the nightly job then read those days as missed and copied the
    # sessions forward.
    existing = await crud.get_training_plan(db, current_user.id)
    base_plan = existing.plan if existing is not None else []
    stored = {
        schemas.session_key(day): day
        for day in base_plan
        if isinstance(day, dict) and day.get("date")
    }
    sanitized: list[schemas.PlanDay] = []
    for index, raw_day in enumerate(body.plan):
        # A malformed day is the client's input, so it is a 422, not the 500 a
        # bare model_validate raised (ops#132).
        try:
            day = schemas.PlanDay.model_validate(raw_day)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"plan day {index} is invalid",
            ) from exc
        kept = stored.get(schemas.session_key(day), {})
        day.source = None
        day.completed = True if kept.get("completed") else None
        day.feedback = _stored_feedback(kept.get("feedback"))
        sanitized.append(day)
    merged = await plan_pipeline.commit_plan(
        db, current_user, sanitized, base_plan=base_plan, source="user_edit"
    )
    return schemas.PlanResponse(plan=merged.plan)


@router.get("/plan-history", response_model=PlanDayHistoryResponse)
async def get_plan_history(
    date: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> PlanDayHistoryResponse:
    """Return the athlete's own per-day plan change log, newest first.

    Optionally filter to a single ``date`` (ISO ``YYYY-MM-DD``). Each entry shows
    the day before/after and which trigger caused it; ``applied=false`` entries
    are automated changes a user pin or completed day blocked. See #343 / #357.
    """
    rows = await crud.list_plan_day_history(db, current_user.id, date=date, limit=limit)
    narrated_batches = await crud.get_narrated_batch_ids(
        db, current_user.id, [row.batch_id for row in rows if row.batch_id]
    )
    return PlanDayHistoryResponse(
        entries=[
            PlanDayHistoryEntry(
                id=row.id,
                date=row.date,
                source=row.source,
                applied=row.applied,
                recorded_at=row.recorded_at,
                batch_id=row.batch_id,
                old_day=row.old_day,
                new_day=row.new_day,
                reason=row.reason,
                narrated=row.batch_id in narrated_batches,
            )
            for row in rows
        ],
        total=len(rows),
    )


@router.get("/plan-history/stats", response_model=PlanDayHistoryStatsResponse)
async def get_plan_history_stats(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> PlanDayHistoryStatsResponse:
    """Aggregate the athlete's plan-change log: counts by trigger, applied vs
    blocked, and the most-changed days. Read-only analytics. See #357."""
    stats = await crud.plan_day_history_stats(db, current_user.id)
    return PlanDayHistoryStatsResponse(
        by_source=stats["by_source"],
        applied_count=stats["applied_count"],
        blocked_count=stats["blocked_count"],
        most_changed_dates=[
            PlanDayHistoryDateCount(date=d["date"], count=d["count"])
            for d in stats["most_changed_dates"]
        ],
        total=stats["total"],
    )


@router.get("/day-board", response_model=schemas.DayBoardResponse)
async def get_day_board(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.DayBoardResponse:
    """The day board the planner gets, with every deduction (ai-trainer-ops#17).

    Built by the same function as the plan prompt's board, so the athlete reads
    what the planner read. ``source`` points at the code that computed it.
    """
    board = await freshness_allocation.board_for_user(
        db, current_user, timezone_name=request_timezone(request)
    )
    source = schemas.CodeSourceSchema(
        path="backend/services/freshness_allocation.py",
        line=inspect.getsourcelines(freshness_allocation.allocate_day)[1],
        symbol="allocate_day",
        ref=settings.git_sha or "develop",
    )
    if board is None:
        return schemas.DayBoardResponse(source=source)
    allocation = board[2]
    return schemas.DayBoardResponse(
        hot=allocation["hot"],
        heat_tolerance=allocation["heat_tolerance"],
        options=[
            schemas.DayBoardOptionSchema(
                **{k: v for k, v in option.items() if k != "components"},
                competes_with_label=(
                    freshness_allocation.demand_label(option["competes_with"])
                    if option["competes_with"]
                    else ""
                ),
            )
            for option in allocation["options"]
        ],
        source=source,
    )


@router.delete("/availability-constraints/{constraint_id}")
async def remove_availability_constraint(
    constraint_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    """Remove an availability constraint the chat captured (ai-trainer-ops#110).

    It was enforced on every plan write, and the only way out was saying "lift
    the constraint" in the chat.
    """
    if not await crud.deactivate_availability_constraint(
        db, current_user.id, constraint_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Constraint not found"
        )
    return {"status": "removed"}


@router.delete("/states/{state_id}")
async def remove_state(
    state_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    """Undo an illness or tired day the chat captured (ai-trainer-ops#18), or
    the heart-rate caution (ai-trainer-ops#184): new sessions may set
    heart-rate targets again."""
    if state_id == "heart-rate-caution":
        current_user.heart_rate_targets_off = False
        await db.commit()
        return {"status": "removed"}
    if not await crud.delete_state(db, current_user.id, state_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="State not found"
        )
    return {"status": "removed"}


@router.delete("/day-preferences/{prefer}/{over}", status_code=204)
async def remove_day_preference(
    prefer: str,
    over: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    """Forget a day preference the chat kept (ai-trainer-ops#204).

    Removing one the profile does not hold is no error: the button under an old
    reply may outlive the preference.
    """
    current_user.day_preferences = [
        p
        for p in day_preference.merged(current_user.day_preferences, [])
        if p != {"prefer": prefer.lower(), "over": over.lower()}
    ]
    await db.flush()
    return Response(status_code=204)
