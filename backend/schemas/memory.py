"""What the coach remembers, the privacy settings, consent and the export."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from schemas.athlete_models import AthleteModelSchema
from schemas.base import CamelModel, _to_camel
from schemas.uncertainty import (
    AthleteExperimentSchema,
    AthleteHypothesisSchema,
    AthleteInquirySchema,
    AthleteOpenQuestionSchema,
    AthletePredictionSchema,
)


# ---------------------------------------------------------------------------
# Coach memory
# ---------------------------------------------------------------------------


class CoachMemoryResponse(BaseModel):
    memory: str


class CoachMemoryRequest(BaseModel):
    memory: str


TrainingTendency = Literal["overtrains", "undertrains", "balanced", "unknown"]


RestResponse = Literal["calm", "restless", "anxious", "relieved", "unknown"]


AdherencePattern = Literal[
    "follows_plan", "negotiates", "adds_extra", "skips", "unknown"
]


class AthleteContextSchema(CamelModel):
    training_tendency: TrainingTendency = "unknown"
    rest_response: RestResponse = "unknown"
    adherence_pattern: AdherencePattern = "unknown"
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    preferred_terrain: list[str] = Field(default_factory=list)
    preferred_session_types: list[str] = Field(default_factory=list)
    coaching_risks: list[str] = Field(default_factory=list)
    notes: str = ""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteContextRequest(AthleteContextSchema):
    pass


AthleteMemoryFactStatus = Literal[
    "active", "stale", "archived", "rejected", "user_confirmed", "needs_validation"
]


# A stable ``fact`` (FTP, max HR, weight) versus an ``observation`` of repeated
# behaviour inferred from training history (#386).
AthleteMemoryFactKind = Literal["fact", "observation"]


class AthleteMemoryFactSchema(CamelModel):
    id: str
    fact: str
    kind: AthleteMemoryFactKind = "observation"
    category: str
    source_snippet: str = ""
    source_exchange_id: Optional[str] = None
    first_observed_at: datetime
    last_confirmed_at: datetime
    confidence: float
    status: AthleteMemoryFactStatus
    contradiction_note: Optional[str] = None
    observation_count: int
    updated_at: datetime

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteMemoryFactsResponse(CamelModel):
    facts: list[AthleteMemoryFactSchema]


class AthleteMemoryFactObservationRequest(CamelModel):
    fact: str = Field(min_length=1)
    kind: AthleteMemoryFactKind = "observation"
    category: str = "general"
    source_snippet: str = ""
    source_exchange_id: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AthleteMemoryFactUpdateRequest(CamelModel):
    fact: Optional[str] = Field(default=None, min_length=1)
    kind: Optional[AthleteMemoryFactKind] = None
    category: Optional[str] = None
    source_snippet: Optional[str] = None
    source_exchange_id: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    status: Optional[AthleteMemoryFactStatus] = None


class MemoryPrivacySettingsSchema(CamelModel):
    memory_updates_enabled: bool


class MemoryPrivacySettingsRequest(CamelModel):
    memory_updates_enabled: bool


class CoachReviewConsentSchema(CamelModel):
    """The athlete's consent to the weekly coach review (ai-trainer-ops#136)."""

    available: bool
    consented: bool
    consented_at: Optional[datetime] = None
    version: str


class CoachReviewConsentRequest(CamelModel):
    consented: bool


class MemoryExportSchema(CamelModel):
    exported_at: datetime
    memory_updates_enabled: bool
    coach_memory: str
    athlete_context: Optional[AthleteContextSchema]
    athlete_model: Optional[AthleteModelSchema] = None
    memory_facts: list[AthleteMemoryFactSchema]
    hypotheses: list[AthleteHypothesisSchema] = []
    open_questions: list[AthleteOpenQuestionSchema] = []
    experiments: list[AthleteExperimentSchema] = []
    predictions: list[AthletePredictionSchema] = []
    inquiries: list[AthleteInquirySchema] = []

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
    )


class ExtractAthleteFactsRequest(CamelModel):
    transcript: str = Field(min_length=1)


class AthleteFactCandidateSchema(CamelModel):
    fact: str
    category: str = "general"
    confidence: float = 0.35
    source_snippet: str = ""


class ExtractAthleteFactsResponse(CamelModel):
    candidates: list[AthleteFactCandidateSchema]
