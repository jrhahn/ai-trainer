"""What the coach remembers, and the athlete's say over it.

The memory text, the athlete context, the motivation and athlete models, the
memory facts, the privacy settings, the coach review consent, and clearing and
exporting all of it.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import get_db
from routers.dependencies import deleted_or_404, updated_or_404
from services import coach_review_consent
from domain import motivation_model as motivation_model_service

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("/coach-memory", response_model=schemas.CoachMemoryResponse)
async def get_coach_memory(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachMemoryResponse:
    memory = await crud.get_coach_memory(db, current_user.id)
    return schemas.CoachMemoryResponse(
        memory=memory.memory if memory is not None else ""
    )


@router.put("/coach-memory", response_model=schemas.CoachMemoryResponse)
async def save_coach_memory(
    body: schemas.CoachMemoryRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachMemoryResponse:
    memory = await crud.upsert_coach_memory(db, current_user.id, body.memory)
    return schemas.CoachMemoryResponse(memory=memory.memory)


@router.get("/athlete-context", response_model=schemas.AthleteContextSchema)
async def get_athlete_context(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteContextSchema:
    context = await crud.get_athlete_context(db, current_user.id)
    if context is None:
        return schemas.AthleteContextSchema()
    return schemas.AthleteContextSchema.model_validate(context, from_attributes=True)


@router.put("/athlete-context", response_model=schemas.AthleteContextSchema)
async def save_athlete_context(
    body: schemas.AthleteContextRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteContextSchema:
    context = await crud.upsert_athlete_context(
        db,
        current_user.id,
        **body.model_dump(),
    )
    return schemas.AthleteContextSchema.model_validate(context, from_attributes=True)


@router.get("/motivation-model", response_model=schemas.AthleteMotivationModelSchema)
async def get_motivation_model(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteMotivationModelSchema:
    """What the coach believes the athlete is optimizing for (#562).

    Always answers with a complete model — an athlete with no stored row gets
    the cold-start defaults rather than a 404, so no caller has to distinguish
    "no motivation model" from "no motivation".
    """
    row = await crud.get_athlete_motivation_model(db, current_user.id)
    payload = crud.motivation_model_as_dict(row)
    payload["updated_at"] = row.updated_at if row is not None else None
    return schemas.AthleteMotivationModelSchema.model_validate(payload)


@router.put("/motivation-model", response_model=schemas.AthleteMotivationModelSchema)
async def save_motivation_model(
    body: schemas.AthleteMotivationModelRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteMotivationModelSchema:
    """The athlete's own edit of their objective (#562, UI in #567).

    Written as ``user_set``, which is what protects it from every later
    inference pass (#563). Omitted fields are left as they are, so a partial
    edit does not blank the rest of the model.
    """
    updates = body.model_dump(exclude_none=True)
    row = await crud.upsert_athlete_motivation_model(
        db,
        current_user.id,
        updates=updates,
        source=motivation_model_service.SOURCE_USER_SET,
    )
    await db.commit()
    await db.refresh(row)
    payload = crud.motivation_model_as_dict(row)
    payload["updated_at"] = row.updated_at
    return schemas.AthleteMotivationModelSchema.model_validate(payload)


@router.get(
    "/motivation-model/weight-history",
    response_model=list[schemas.MotivationWeightEventSchema],
)
async def get_motivation_weight_history(
    limit: int = 20,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> list[schemas.MotivationWeightEventSchema]:
    """Why the athlete's weight balance looks the way it does (#566).

    Newest first. An athlete who can see their weights (#567) but not what moved
    them is being asked to trust a number that changes on its own.
    """
    events = await crud.get_motivation_weight_history(
        db, current_user.id, limit=max(1, min(limit, 100))
    )
    return [
        schemas.MotivationWeightEventSchema.model_validate(event, from_attributes=True)
        for event in events
    ]


@router.get("/athlete-model", response_model=schemas.AthleteModelSchema)
async def get_athlete_model(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteModelSchema:
    model = await crud.get_athlete_model(db, current_user.id)
    if model is None:
        return schemas.AthleteModelSchema()
    return schemas.AthleteModelSchema.model_validate(model, from_attributes=True)


@router.put("/athlete-model", response_model=schemas.AthleteModelSchema)
async def save_athlete_model(
    body: schemas.AthleteModelRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteModelSchema:
    model = await crud.upsert_athlete_model(
        db,
        current_user.id,
        **body.model_dump(),
    )
    return schemas.AthleteModelSchema.model_validate(model, from_attributes=True)


@router.get("/athlete-memory-facts", response_model=schemas.AthleteMemoryFactsResponse)
async def list_athlete_memory_facts(
    include_inactive: bool = Query(False, alias="includeInactive"),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteMemoryFactsResponse:
    facts = await crud.list_athlete_memory_facts(
        db, current_user.id, include_inactive=include_inactive
    )
    return schemas.AthleteMemoryFactsResponse(
        facts=[
            schemas.AthleteMemoryFactSchema.model_validate(fact, from_attributes=True)
            for fact in facts
        ]
    )


@router.post(
    "/athlete-memory-facts",
    response_model=schemas.AthleteMemoryFactSchema,
    status_code=status.HTTP_201_CREATED,
)
async def observe_athlete_memory_fact(
    body: schemas.AthleteMemoryFactObservationRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteMemoryFactSchema:
    fact = await crud.observe_athlete_memory_fact(
        db,
        current_user.id,
        **body.model_dump(),
    )
    return schemas.AthleteMemoryFactSchema.model_validate(fact, from_attributes=True)


@router.patch(
    "/athlete-memory-facts/{fact_id}",
    response_model=schemas.AthleteMemoryFactSchema,
)
async def correct_athlete_memory_fact(
    fact_id: str,
    body: schemas.AthleteMemoryFactUpdateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteMemoryFactSchema:
    fact = await updated_or_404(
        crud.update_athlete_memory_fact(
            db, current_user.id, fact_id, **body.model_dump(exclude_unset=True)
        )
    )
    return schemas.AthleteMemoryFactSchema.model_validate(fact, from_attributes=True)


@router.delete(
    "/athlete-memory-facts/{fact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_athlete_memory_fact(
    fact_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    return await deleted_or_404(
        crud.delete_athlete_memory_fact(db, current_user.id, fact_id)
    )


@router.get("/memory-privacy", response_model=schemas.MemoryPrivacySettingsSchema)
async def get_memory_privacy_settings(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.MemoryPrivacySettingsSchema:
    return schemas.MemoryPrivacySettingsSchema(
        memory_updates_enabled=current_user.memory_updates_enabled
    )


@router.put("/memory-privacy", response_model=schemas.MemoryPrivacySettingsSchema)
async def update_memory_privacy_settings(
    body: schemas.MemoryPrivacySettingsRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.MemoryPrivacySettingsSchema:
    current_user.memory_updates_enabled = body.memory_updates_enabled
    await db.flush()
    await db.commit()
    return schemas.MemoryPrivacySettingsSchema(
        memory_updates_enabled=current_user.memory_updates_enabled
    )


def _coach_review_consent_schema(
    consent: models.CoachReviewConsent | None,
) -> schemas.CoachReviewConsentSchema:
    return schemas.CoachReviewConsentSchema(
        available=settings.coach_review_opt_in,
        consented=consent is not None,
        # SQLite hands a stored time back without its zone; it is UTC.
        consented_at=(
            consent.given_at.replace(tzinfo=consent.given_at.tzinfo or timezone.utc)
            if consent
            else None
        ),
        version=coach_review_consent.VERSION,
    )


@router.get("/coach-review-consent", response_model=schemas.CoachReviewConsentSchema)
async def get_coach_review_consent(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachReviewConsentSchema:
    """Whether the athlete agreed to the weekly coach review (ai-trainer-ops#136)."""
    if not settings.coach_review_opt_in:
        return _coach_review_consent_schema(None)
    return _coach_review_consent_schema(
        await coach_review_consent.valid(db, current_user.id)
    )


@router.put("/coach-review-consent", response_model=schemas.CoachReviewConsentSchema)
async def update_coach_review_consent(
    body: schemas.CoachReviewConsentRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachReviewConsentSchema:
    """Give or withdraw the consent; 404 while the opt-in is switched off."""
    if not settings.coach_review_opt_in:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.consented:
        consent = await coach_review_consent.give(db, current_user.id)
    else:
        await coach_review_consent.withdraw(db, current_user.id)
        consent = None
    await db.commit()
    return _coach_review_consent_schema(consent)


@router.delete("/memory", status_code=status.HTTP_204_NO_CONTENT)
async def clear_all_memory(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    await crud.clear_athlete_memory(db, current_user.id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/memory-export", response_model=schemas.MemoryExportSchema)
async def export_memory(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.MemoryExportSchema:
    coach_memory_row = await crud.get_coach_memory(db, current_user.id)
    athlete_context_row = await crud.get_athlete_context(db, current_user.id)
    athlete_model_row = await crud.get_athlete_model(db, current_user.id)
    facts = await crud.list_athlete_memory_facts(
        db, current_user.id, include_inactive=True
    )
    hypotheses = await crud.list_athlete_hypotheses(
        db, current_user.id, include_resolved=True
    )
    open_questions = await crud.list_athlete_open_questions(
        db, current_user.id, include_resolved=True
    )
    experiments = await crud.list_athlete_experiments(
        db, current_user.id, include_resolved=True
    )
    predictions = await crud.list_athlete_predictions(
        db, current_user.id, include_resolved=True
    )
    inquiries = await crud.list_athlete_inquiries(
        db, current_user.id, include_resolved=True
    )
    return schemas.MemoryExportSchema(
        exported_at=datetime.now(timezone.utc),
        memory_updates_enabled=current_user.memory_updates_enabled,
        coach_memory=coach_memory_row.memory if coach_memory_row is not None else "",
        athlete_context=(
            schemas.AthleteContextSchema.model_validate(
                athlete_context_row, from_attributes=True
            )
            if athlete_context_row is not None
            else None
        ),
        athlete_model=(
            schemas.AthleteModelSchema.model_validate(
                athlete_model_row, from_attributes=True
            )
            if athlete_model_row is not None
            else None
        ),
        memory_facts=[
            schemas.AthleteMemoryFactSchema.model_validate(f, from_attributes=True)
            for f in facts
        ],
        hypotheses=[
            schemas.AthleteHypothesisSchema.model_validate(h, from_attributes=True)
            for h in hypotheses
        ],
        open_questions=[
            schemas.AthleteOpenQuestionSchema.model_validate(q, from_attributes=True)
            for q in open_questions
        ],
        experiments=[
            schemas.AthleteExperimentSchema.model_validate(e, from_attributes=True)
            for e in experiments
        ],
        predictions=[
            schemas.AthletePredictionSchema.model_validate(p, from_attributes=True)
            for p in predictions
        ],
        inquiries=[
            schemas.AthleteInquirySchema.model_validate(i, from_attributes=True)
            for i in inquiries
        ],
    )
