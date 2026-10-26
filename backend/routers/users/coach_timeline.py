"""What the coach believes and asks, and the timeline that shows it.

Hypotheses, open questions, inquiries, validation experiments and predictions.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import get_db
from routers.dependencies import deleted_or_404, updated_or_404
from services import athlete_inquiry
from services.athlete_language import interface_language
from routers.users.plan import PlanDayHistoryEntry, get_plan_history

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("/athlete-hypotheses", response_model=schemas.AthleteHypothesesResponse)
async def list_athlete_hypotheses(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteHypothesesResponse:
    hypotheses = await crud.list_athlete_hypotheses(
        db, current_user.id, include_resolved=include_resolved
    )
    return schemas.AthleteHypothesesResponse(
        hypotheses=[
            schemas.AthleteHypothesisSchema.model_validate(h, from_attributes=True)
            for h in hypotheses
        ]
    )


@router.patch(
    "/athlete-hypotheses/{hypothesis_id}",
    response_model=schemas.AthleteHypothesisSchema,
)
async def update_athlete_hypothesis(
    hypothesis_id: str,
    body: schemas.AthleteHypothesisUpdateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteHypothesisSchema:
    hypothesis = await updated_or_404(
        crud.update_athlete_hypothesis(
            db, current_user.id, hypothesis_id, **body.model_dump(exclude_unset=True)
        )
    )
    return schemas.AthleteHypothesisSchema.model_validate(
        hypothesis, from_attributes=True
    )


@router.delete(
    "/athlete-hypotheses/{hypothesis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_athlete_hypothesis(
    hypothesis_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    return await deleted_or_404(
        crud.delete_athlete_hypothesis(db, current_user.id, hypothesis_id)
    )


@router.get("/open-questions", response_model=schemas.AthleteOpenQuestionsResponse)
async def list_athlete_open_questions(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteOpenQuestionsResponse:
    questions = await crud.list_athlete_open_questions(
        db, current_user.id, include_resolved=include_resolved
    )
    return schemas.AthleteOpenQuestionsResponse(
        open_questions=[
            schemas.AthleteOpenQuestionSchema.model_validate(q, from_attributes=True)
            for q in questions
        ]
    )


@router.patch(
    "/open-questions/{question_id}",
    response_model=schemas.AthleteOpenQuestionSchema,
)
async def update_athlete_open_question(
    question_id: str,
    body: schemas.AthleteOpenQuestionUpdateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteOpenQuestionSchema:
    question = await updated_or_404(
        crud.update_athlete_open_question(
            db, current_user.id, question_id, **body.model_dump(exclude_unset=True)
        )
    )
    return schemas.AthleteOpenQuestionSchema.model_validate(
        question, from_attributes=True
    )


@router.delete(
    "/open-questions/{question_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_athlete_open_question(
    question_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    return await deleted_or_404(
        crud.delete_athlete_open_question(db, current_user.id, question_id)
    )


@router.get("/inquiries", response_model=schemas.AthleteInquiriesResponse)
async def list_athlete_inquiries(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteInquiriesResponse:
    inquiries = await crud.list_athlete_inquiries(
        db, current_user.id, include_resolved=include_resolved
    )
    return schemas.AthleteInquiriesResponse(
        inquiries=[
            schemas.AthleteInquirySchema.model_validate(i, from_attributes=True)
            for i in inquiries
        ]
    )


@router.post(
    "/inquiries/{inquiry_id}/answer",
    response_model=schemas.AthleteInquiryAnswerResponse,
)
async def answer_athlete_inquiry(
    inquiry_id: str,
    body: schemas.AthleteInquiryAnswerRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteInquiryAnswerResponse:
    """Answer a pinned inquiry (#506).

    The answer is judged, and the reply the coach gives back is persisted into the
    chat so the exchange stays part of the conversation rather than vanishing with
    the pin.
    """
    inquiry = await crud.get_athlete_inquiry(db, current_user.id, inquiry_id)
    if inquiry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if inquiry.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This question is no longer waiting for an answer",
        )

    question_asked = inquiry.question
    try:
        (
            inquiry,
            accepted,
            coach_reply,
            asks_coach,
        ) = await athlete_inquiry.submit_inquiry_answer(
            db,
            current_user,
            inquiry,
            body.answer,
            option=body.option,
            interface_language=interface_language(request),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # Record the exchange in the chat: the question as the coach asked it, the
    # athlete's answer, and what the coach said back. Without this the answered
    # question disappears from the timeline entirely once the pin clears.
    now_iso = datetime.now(timezone.utc).isoformat()
    await crud.create_chat_message(
        db, current_user.id, role="assistant", content=question_asked, timestamp=now_iso
    )
    # A tapped answer shows as the label it stands for (ai-trainer-ops#155).
    said = (
        inquiry.answer
        if body.option is not None and inquiry.answer
        else body.answer.strip()
    )
    await crud.create_chat_message(
        db, current_user.id, role="user", content=said, timestamp=now_iso
    )
    if coach_reply.strip():
        await crud.create_chat_message(
            db,
            current_user.id,
            role="assistant",
            content=coach_reply.strip(),
            timestamp=now_iso,
        )

    return schemas.AthleteInquiryAnswerResponse(
        inquiry=schemas.AthleteInquirySchema.model_validate(
            inquiry, from_attributes=True
        ),
        accepted=accepted,
        coach_reply=coach_reply,
        forward_to_coach=asks_coach,
    )


@router.post(
    "/inquiries/{inquiry_id}/dismiss", response_model=schemas.AthleteInquirySchema
)
async def dismiss_athlete_inquiry(
    inquiry_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteInquirySchema:
    inquiry = await crud.dismiss_athlete_inquiry(db, current_user.id, inquiry_id)
    if inquiry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return schemas.AthleteInquirySchema.model_validate(inquiry, from_attributes=True)


@router.delete("/inquiries/{inquiry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_athlete_inquiry(
    inquiry_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    return await deleted_or_404(
        crud.delete_athlete_inquiry(db, current_user.id, inquiry_id)
    )


@router.get(
    "/validation-experiments", response_model=schemas.AthleteExperimentsResponse
)
async def list_validation_experiments(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteExperimentsResponse:
    experiments = await crud.list_athlete_experiments(
        db, current_user.id, include_resolved=include_resolved
    )
    return schemas.AthleteExperimentsResponse(
        experiments=[
            schemas.AthleteExperimentSchema.model_validate(e, from_attributes=True)
            for e in experiments
        ]
    )


class CoachTimelineResponse(schemas.CamelModel):
    """The coach chat's timeline in one response (ai-trainer-ops#277)."""

    plan_history: list[PlanDayHistoryEntry]
    open_questions: list[schemas.AthleteOpenQuestionSchema]
    hypotheses: list[schemas.AthleteHypothesisSchema]
    experiments: list[schemas.AthleteExperimentSchema]
    inquiries: list[schemas.AthleteInquirySchema]


@router.get("/coach-timeline", response_model=CoachTimelineResponse)
async def get_coach_timeline(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> CoachTimelineResponse:
    """What the coach chat's timeline shows, in one request (ai-trainer-ops#277).

    The chat fetched five lists after every reply: the plan history and the
    open questions, hypotheses, experiments and pinned questions. These are the
    lists those five routes return with their defaults, built by the routes
    themselves, so the two cannot drift apart.
    """
    history = await get_plan_history(
        date=None, limit=200, db=db, current_user=current_user
    )
    questions = await list_athlete_open_questions(
        include_resolved=False, db=db, current_user=current_user
    )
    hypotheses = await list_athlete_hypotheses(
        include_resolved=False, db=db, current_user=current_user
    )
    experiments = await list_validation_experiments(
        include_resolved=False, db=db, current_user=current_user
    )
    inquiries = await list_athlete_inquiries(
        include_resolved=False, db=db, current_user=current_user
    )
    return CoachTimelineResponse(
        plan_history=history.entries,
        open_questions=questions.open_questions,
        hypotheses=hypotheses.hypotheses,
        experiments=experiments.experiments,
        inquiries=inquiries.inquiries,
    )


@router.patch(
    "/validation-experiments/{experiment_id}",
    response_model=schemas.AthleteExperimentSchema,
)
async def update_validation_experiment(
    experiment_id: str,
    body: schemas.AthleteExperimentUpdateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteExperimentSchema:
    experiment = await updated_or_404(
        crud.update_athlete_experiment(
            db, current_user.id, experiment_id, **body.model_dump(exclude_unset=True)
        )
    )
    return schemas.AthleteExperimentSchema.model_validate(
        experiment, from_attributes=True
    )


@router.delete(
    "/validation-experiments/{experiment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_validation_experiment(
    experiment_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    return await deleted_or_404(
        crud.delete_athlete_experiment(db, current_user.id, experiment_id)
    )


@router.get("/predictions", response_model=schemas.AthletePredictionsResponse)
async def list_athlete_predictions(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthletePredictionsResponse:
    predictions = await crud.list_athlete_predictions(
        db, current_user.id, include_resolved=include_resolved
    )
    evaluated, correct = await crud.get_athlete_prediction_accuracy(db, current_user.id)
    return schemas.AthletePredictionsResponse(
        predictions=[
            schemas.AthletePredictionSchema.model_validate(p, from_attributes=True)
            for p in predictions
        ],
        accuracy=schemas.AthletePredictionAccuracy(
            evaluated=evaluated,
            correct=correct,
            accuracy=(correct / evaluated) if evaluated else None,
        ),
    )


@router.patch(
    "/predictions/{prediction_id}",
    response_model=schemas.AthletePredictionSchema,
)
async def update_athlete_prediction(
    prediction_id: str,
    body: schemas.AthletePredictionUpdateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthletePredictionSchema:
    prediction = await updated_or_404(
        crud.update_athlete_prediction(
            db, current_user.id, prediction_id, **body.model_dump(exclude_unset=True)
        )
    )
    return schemas.AthletePredictionSchema.model_validate(
        prediction, from_attributes=True
    )


@router.delete(
    "/predictions/{prediction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_athlete_prediction(
    prediction_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    return await deleted_or_404(
        crud.delete_athlete_prediction(db, current_user.id, prediction_id)
    )
