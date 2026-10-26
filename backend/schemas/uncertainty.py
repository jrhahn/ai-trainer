"""Hypotheses, open questions, inquiries, experiments and predictions."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import ConfigDict, Field, field_validator
from schemas.base import CamelModel, _to_camel


AthleteHypothesisStatus = Literal["proposed", "confirmed", "refuted"]


# An issue number of the project's tracker. Starting hypotheses stored before
# ai-trainer-ops#215 carry one in their rationale, and it is no text for an
# athlete.
_ISSUE_REFERENCE = re.compile(r"\s*\(ai-trainer-ops#\d+\)")


def without_issue_reference(text: str) -> str:
    return _ISSUE_REFERENCE.sub("", text)


class AthleteHypothesisSchema(CamelModel):
    id: str
    statement: str
    category: str
    rationale: str = ""
    confidence: float
    # Structured supporting evidence and the competing explanations still to be
    # ruled out (#479). Deterministic performance-model hypotheses populate these;
    # older LLM-formed hypotheses leave them empty (stored NULL -> []).
    evidence: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    evidence_count: int
    status: AthleteHypothesisStatus
    first_proposed_at: datetime
    updated_at: datetime

    @field_validator("evidence", "alternative_explanations", mode="before")
    @classmethod
    def _default_list(cls, value: object) -> object:
        return value or []

    @field_validator("rationale", mode="before")
    @classmethod
    def _shown_rationale(cls, value: object) -> object:
        return without_issue_reference(value) if isinstance(value, str) else value

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteHypothesesResponse(CamelModel):
    hypotheses: list[AthleteHypothesisSchema]


class AthleteHypothesisUpdateRequest(CamelModel):
    statement: Optional[str] = Field(default=None, min_length=1)
    category: Optional[str] = None
    rationale: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    status: Optional[AthleteHypothesisStatus] = None


AthleteOpenQuestionStatus = Literal["open", "answered", "dismissed"]


class AthleteOpenQuestionSchema(CamelModel):
    """An open question the coach is tracking about the athlete (#385)."""

    id: str
    question: str
    category: str
    evidence: str = ""
    needs: str = ""
    evidence_count: int
    status: AthleteOpenQuestionStatus
    resolution: Optional[str] = None
    first_asked_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteOpenQuestionsResponse(CamelModel):
    open_questions: list[AthleteOpenQuestionSchema]


class AthleteOpenQuestionUpdateRequest(CamelModel):
    question: Optional[str] = Field(default=None, min_length=1)
    category: Optional[str] = None
    evidence: Optional[str] = None
    needs: Optional[str] = None
    resolution: Optional[str] = None
    status: Optional[AthleteOpenQuestionStatus] = None


AthleteInquiryStatus = Literal["pending", "answered", "needs_settings", "dismissed"]


class AthleteInquirySchema(CamelModel):
    """A question the coach put to the athlete because data cannot answer it (#506)."""

    id: str
    question: str
    category: str
    why_asking: str = ""
    settings_hint: str = ""
    status: AthleteInquiryStatus
    answer: Optional[str] = None
    ask_count: int
    follow_up_note: Optional[str] = None
    asked_at: datetime
    answered_at: Optional[datetime] = None
    updated_at: datetime
    # The answers the athlete can tap, as labels; the stance each carries stays
    # on the server (ai-trainer-ops#155).
    options: Optional[list[str]] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )

    @field_validator("options", mode="before")
    @classmethod
    def _labels(cls, value: object) -> list[str] | None:
        if not value:
            return None
        return [
            str(item.get("label") if isinstance(item, dict) else item)
            for item in value  # type: ignore[union-attr]
        ]


class AthleteInquiriesResponse(CamelModel):
    inquiries: list[AthleteInquirySchema]


class AthleteInquiryAnswerRequest(CamelModel):
    answer: str = Field(min_length=1)
    # The tapped answer, by its place in ``options`` (ai-trainer-ops#155). The
    # stored label is what counts; ``answer`` is then only what the chat shows.
    option: Optional[int] = Field(default=None, ge=0)


class AthleteInquiryAnswerResponse(CamelModel):
    """The inquiry after the answer was judged, plus what the coach said back.

    ``accepted`` reports whether the answer resolved the question. When it did
    not, ``coach_reply`` carries either the rephrased ask or the hand-off to
    Settings, so the chat can show the coach's words without a second round trip.
    """

    inquiry: AthleteInquirySchema
    accepted: bool
    coach_reply: str = ""
    # The answer also asks for something only the coach chat can do, such as a
    # plan change. The app then sends the same text to the chat as well
    # (ai-trainer-ops#103 Q28).
    forward_to_coach: bool = False


AthleteExperimentStatus = Literal["suggested", "completed", "dismissed"]


class AthleteExperimentSchema(CamelModel):
    id: str
    hypothesis_id: Optional[str] = None
    question: str
    protocol: str
    rationale: str = ""
    category: str
    status: AthleteExperimentStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteExperimentsResponse(CamelModel):
    experiments: list[AthleteExperimentSchema]


class AthleteExperimentUpdateRequest(CamelModel):
    question: Optional[str] = Field(default=None, min_length=1)
    protocol: Optional[str] = Field(default=None, min_length=1)
    rationale: Optional[str] = None
    category: Optional[str] = None
    status: Optional[AthleteExperimentStatus] = None


AthletePredictionStatus = Literal["pending", "correct", "incorrect"]


class AthletePredictionSchema(CamelModel):
    id: str
    prediction: str
    expected_outcome: str
    actual_outcome: Optional[str] = None
    horizon: str = ""
    category: str
    confidence: float
    status: AthletePredictionStatus
    created_at: datetime
    evaluated_at: Optional[datetime] = None
    updated_at: datetime

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthletePredictionAccuracy(CamelModel):
    """Coaching-quality summary: how many predictions were checked and hit."""

    evaluated: int
    correct: int
    accuracy: Optional[float] = None


class AthletePredictionsResponse(CamelModel):
    predictions: list[AthletePredictionSchema]
    accuracy: AthletePredictionAccuracy


class AthletePredictionUpdateRequest(CamelModel):
    prediction: Optional[str] = Field(default=None, min_length=1)
    expected_outcome: Optional[str] = Field(default=None, min_length=1)
    actual_outcome: Optional[str] = None
    horizon: Optional[str] = None
    category: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    status: Optional[AthletePredictionStatus] = None
