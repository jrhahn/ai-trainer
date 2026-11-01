"""What the chat and the activity analysis send and answer."""

from __future__ import annotations

import json
from typing import ClassVar, Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from schemas.base import CamelModel
from schemas.chat import (
    DaySummarySchema,
    ProfileProposalSchema,
    RaceOfferSchema,
    RaceProposalSchema,
    ReplyNoteSchema,
)
from schemas.plan import CONTEXT_WORKOUT_MAX_CHARS, CodeSourceSchema
from schemas.plan_day import PlanDayUpdateSchema, TrainingDaySchema
from schemas.profile import (
    DayPreferenceSchema,
    RiderAssessmentSchema,
)
from schemas.races import RaceEventResponse
from schemas.rides import StravaActivitySchema


class AnalyseActivitiesRequest(CamelModel):
    activities: list[StravaActivitySchema]
    max_heart_rate: Optional[int] = None
    current_ftp: Optional[int] = None
    source: Literal["strava", "intervals"] = "strava"


class AskTrainerRequest(CamelModel):
    # Bounded so a single message cannot carry ~75k tokens of model spend or an
    # empty bubble (ops#132). 8000 chars is ~2k tokens — generous for a typed
    # question; the workout context rides in context_workout, not here.
    question: str = Field(min_length=1, max_length=8000)
    context_workout: Optional[Any] = None
    # The question is a question-card answer the chat already holds, sent on to
    # the coach (ai-trainer-ops#103 Q28). It is not stored a second time.
    already_in_chat: bool = False

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped

    @field_validator("context_workout")
    @classmethod
    def _context_workout_within_bounds(cls, value: Any) -> Any:
        if (
            value is not None
            and len(json.dumps(value, default=str)) > CONTEXT_WORKOUT_MAX_CHARS
        ):
            raise ValueError(
                f"the workout context is limited to {CONTEXT_WORKOUT_MAX_CHARS} characters"
            )
        return value


class AnalyseActivitiesResponse(CamelModel):
    """Response for the /ai/analyse-activities endpoint.

    Wraps the rider assessment together with optional plan updates so the
    frontend can apply targeted training-plan changes immediately after a
    new ride is analysed.
    """

    assessment: RiderAssessmentSchema
    plan_updates: Optional[list[PlanDayUpdateSchema]] = None


class RideLabelUpdateSchema(CamelModel):
    strava_activity_id: int
    label_override: str


class CapturedGoalSchema(CamelModel):
    """A goal the chat just stored, shown for the athlete to keep or undo (ops#65)."""

    id: str
    label: str


class CapturedConstraintSchema(CamelModel):
    """An availability constraint the chat just stored (ai-trainer-ops#110).

    Enforced on every plan write, so it is shown to keep or remove.
    """

    id: str
    label: str
    removable: bool = True


class CapturedStateSchema(CamelModel):
    """An illness or a tired day the chat just stored (ai-trainer-ops#18).

    ``removable`` is false for "well again", which ends an illness rather than
    adding one: there is nothing to take back with a button.
    """

    id: str
    label: str
    removable: bool = True


class CoachSawSchema(CamelModel):
    """One deterministic block the coach was given, with its code (ops#17, #81 Q24)."""

    key: str
    title: str
    text: str
    source: CodeSourceSchema


class AskTrainerResponse(CamelModel):
    response: str
    # What the app says under the reply, beside it rather than in it (#262).
    notes: list[ReplyNoteSchema] = []
    # The coach's sentence per day the reply changed, shown under it
    # (ai-trainer-ops#266).
    day_summaries: list[DaySummarySchema] = []
    # Written a second time because a check found something (ai-trainer-ops#87).
    rewritten: bool = False
    # Live only, like the confirmations: not stored with the chat.
    coach_saw: list[CoachSawSchema] = []
    captured_goals: list[CapturedGoalSchema] = []
    captured_states: list[CapturedStateSchema] = []
    captured_constraints: list[CapturedConstraintSchema] = []
    captured_day_preferences: list[DayPreferenceSchema] = []
    race_proposal: Optional[RaceProposalSchema] = None
    race_offer: Optional[RaceOfferSchema] = None
    profile_proposal: Optional[ProfileProposalSchema] = None
    # The stored reply's id, to answer its offers by (ai-trainer-ops#338 T1).
    message_id: Optional[str] = None
    plan_updates: Optional[list[PlanDayUpdateSchema]] = None
    updated_plan: Optional[list[Any]] = None
    sources: Optional[list[Any]] = None
    ride_label_updates: Optional[list[RideLabelUpdateSchema]] = None
    physiology_rationale: Optional[str] = None
    context_rationale: Optional[str] = None
    objective_rationale: Optional[str] = None


class RateWorkoutRequest(CamelModel):
    """Rate a completed workout against its planned day.

    Deliberately *not* a :class:`RideIdentityRequest`. ``strava_activity_id``
    here is handed straight to Strava's own stream endpoint rather than used to
    look up one of our stored rides, and it only runs when the athlete has a
    Strava token. Strava's ids are well inside float64, so nothing rounds. The
    opt-out is explicit so the identity guardrail records the decision instead of
    having a hole in it.
    """

    identifies_stored_ride: ClassVar[bool] = False

    day: TrainingDaySchema
    strava_activity_id: Optional[int] = None


class RateWorkoutResponse(BaseModel):
    feedback: str
    flag_for_adaptation: bool = False
    needs_athlete_feedback: bool = False
    follow_up_question: Optional[str] = None
    suggested_feedback_tags: list[str] = []


class RefreshKnowledgeResponse(BaseModel):
    status: str
    message: str


class RefreshLoginSummaryResponse(CamelModel):
    login_summary: str


class TrainingStatusResponse(CamelModel):
    """The dashboard status chip plus the coach's reason for it (#499)."""

    label: Optional[str] = None
    tone: Optional[str] = None
    rationale: Optional[str] = None


class RaceEventFeedbackRequest(CamelModel):
    event: RaceEventResponse
    action: str = "added"


class RaceEventFeedbackResponse(CamelModel):
    feedback: str
