"""Strength sessions: one day's session, the logged exercises and their e1RM trend."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import get_db
from domain import strength_model

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("/workouts/{date}/strength", response_model=schemas.StrengthSessionResponse)
async def get_strength_session(
    date: str,
    slot: int = 0,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.StrengthSessionResponse:
    """The sets logged for one gym session, with the figures derived from them (#714).

    Derived on read rather than stored: tonnage, e1RM and %e1RM all come from
    ``domain.strength_model``, so correcting a formula corrects every
    historical figure instead of leaving stored values behind it.
    """
    normalized_slot = schemas.normalize_slot(slot)
    rows = await crud.get_strength_sets(db, current_user.id, date, normalized_slot)
    sets = [strength_model.set_from_row(row) for row in rows]

    # %e1RM is relative to the best estimate *this session* produced for that
    # exercise, which is what makes a back-off set legible as "80 % of today's
    # top set" rather than as a bare weight.
    best = strength_model.best_e1rm_per_exercise(sets)

    return schemas.StrengthSessionResponse(
        date=date,
        slot=normalized_slot,
        sets=[
            schemas.StrengthSetResultSchema(
                exercise=row.exercise,
                set_index=row.set_index,
                reps=row.reps,
                weight_kg=row.weight_kg,
                rir=row.rir,
                rpe=row.rpe,
                e1rm_kg=_rounded(
                    strength_model.e1rm(row.weight_kg, row.reps, rir=row.rir)
                ),
                e1rm_confident=strength_model.e1rm_is_confident(row.reps, row.rir),
                relative_intensity_pct=_rounded(
                    strength_model.relative_intensity(
                        row.weight_kg, best.get(row.exercise)
                    )
                ),
            )
            for row in rows
        ],
        volume_load_kg=round(strength_model.volume_load(sets), 1),
        best_e1rm_kg={name: round(value, 1) for name, value in best.items()},
    )


def _rounded(value: float | None, digits: int = 1) -> float | None:
    """Round for display, keeping ``None`` as ``None``.

    An absent estimate is not 0.0 — see ``strength_model.e1rm`` — so this must
    not coerce it into one on the way out.
    """
    return None if value is None else round(value, digits)


@router.get("/strength/exercises", response_model=schemas.LoggedExercisesResponse)
async def get_logged_exercises(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.LoggedExercisesResponse:
    """The exercises this athlete has actually logged (#714).

    Their own lifts rather than a hardcoded list of exercises someone else
    thought they should be doing.
    """
    return schemas.LoggedExercisesResponse(
        exercises=await crud.get_logged_exercises(db, current_user.id)
    )


@router.get("/strength/e1rm/{exercise}", response_model=schemas.E1rmTrendResponse)
async def get_e1rm_trend(
    exercise: str,
    formula: str = strength_model.DEFAULT_E1RM_FORMULA,
    days: int = 365,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.E1rmTrendResponse:
    """How one exercise has progressed, one point per session (#714).

    Progression is tracked on the *estimate* and never on a true single-rep
    test: a 1RM attempt costs a cyclist a day of training and carries an injury
    risk the estimate does not.
    """
    if formula not in strength_model.E1RM_FORMULAS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Unknown e1RM formula. Expected one of: "
                f"{', '.join(strength_model.E1RM_FORMULAS)}."
            ),
        )

    normalized = strength_model.normalize_exercise_name(exercise)
    rows = await crud.get_strength_sets_for_exercise(
        db,
        current_user.id,
        normalized,
        # Clamped at both ends: the window is turned into a ``timedelta``, and an
        # unbounded value from the query string overflows it into a 500. Ten
        # years is past any athlete's logged history here, so the ceiling costs
        # nothing real.
        limit_days=max(1, min(int(days), 3650)),
    )
    points = strength_model.e1rm_trend(
        ((row.date, strength_model.set_from_row(row)) for row in rows),
        formula=formula,
    )
    return schemas.E1rmTrendResponse(
        exercise=normalized,
        formula=formula,
        points=[
            schemas.E1rmPointSchema(
                date=point.date,
                e1rm_kg=round(point.e1rm_kg, 1),
                confident=point.confident,
                reps=point.reps,
                weight_kg=point.weight_kg,
                rir=point.rir,
            )
            for point in points
        ],
    )
