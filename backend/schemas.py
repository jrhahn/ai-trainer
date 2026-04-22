"""Pydantic request/response schemas.

All schemas that are exchanged with the TypeScript frontend use camelCase
field names via a custom alias generator that preserves acronyms (FTP, HR).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


# ---------------------------------------------------------------------------
# Alias generator: snake_case → camelCase, preserving FTP / HR as all-caps
# ---------------------------------------------------------------------------

_UPPER_ACRONYMS = {"ftp", "hr"}


def _to_camel(name: str) -> str:
    parts = name.split("_")
    result = parts[0].lower()
    for part in parts[1:]:
        lower = part.lower()
        result += lower.upper() if lower in _UPPER_ACRONYMS else lower.capitalize()
    return result


class CamelModel(BaseModel):
    """Base model that serialises to camelCase JSON."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Rider assessment
# ---------------------------------------------------------------------------


class RiderAssessmentSchema(CamelModel):
    estimated_ftp: Optional[int] = None
    estimated_threshold_hr: Optional[int] = None
    rider_type: str
    notes: str
    hr_zones: Optional[Any] = None
    ride_insights: Optional[str] = None
    last_ride_feedback: Optional[str] = None
    login_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# User / profile
# ---------------------------------------------------------------------------


class StravaConnectionSchema(CamelModel):
    athlete_id: int
    athlete_name: str


class UserResponse(CamelModel):
    id: str
    email: str
    name: Optional[str] = None
    is_onboarded: bool
    strava_analysis_complete: bool
    last_strava_activity_id: Optional[int] = None
    # profile fields
    bike_type: Optional[str] = None
    training_goal: Optional[str] = None
    race_date: Optional[str] = None
    race_description: Optional[str] = None
    weekly_hours: Optional[float] = None
    follows_training_plan: bool = False
    resting_heart_rate: Optional[int] = None
    max_heart_rate: Optional[int] = None
    threshold_heart_rate: Optional[int] = None
    current_ftp: Optional[int] = None
    fitness_level: Optional[str] = None
    ai_provider: str = "openai"
    # related
    rider_assessment: Optional[RiderAssessmentSchema] = None
    strava_connection: Optional[StravaConnectionSchema] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class UpdateProfileRequest(CamelModel):
    """All fields optional – PATCH-style partial update via PUT."""

    name: Optional[str] = None
    bike_type: Optional[str] = None
    training_goal: Optional[str] = None
    race_date: Optional[str] = None
    race_description: Optional[str] = None
    weekly_hours: Optional[float] = None
    follows_training_plan: Optional[bool] = None
    resting_heart_rate: Optional[int] = None
    max_heart_rate: Optional[int] = None
    threshold_heart_rate: Optional[int] = None
    current_ftp: Optional[int] = None
    fitness_level: Optional[str] = None
    ai_provider: Optional[str] = None
    is_onboarded: Optional[bool] = None
    strava_analysis_complete: Optional[bool] = None
    last_strava_activity_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Training plan
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    """Frontend sends the plan as an array of raw JSON dicts (camelCase from AI)."""

    plan: list[Any]


class PlanResponse(BaseModel):
    plan: list[Any]


# ---------------------------------------------------------------------------
# Workout logs
# ---------------------------------------------------------------------------


class WorkoutFeedbackSchema(CamelModel):
    actual_duration_minutes: int
    average_power: Optional[int] = None
    average_heart_rate: Optional[int] = None
    peak_power: Optional[int] = None
    perceived_effort: int
    notes: str = ""
    completed_at: str


class WorkoutLogRequest(BaseModel):
    feedback: WorkoutFeedbackSchema


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatMessageSchema(CamelModel):
    role: str
    content: str
    timestamp: str
    plan_update_count: Optional[int] = None


class ChatMessageRequest(CamelModel):
    role: str
    content: str
    timestamp: str
    plan_update_count: Optional[int] = None


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageSchema]


# ---------------------------------------------------------------------------
# Coach memory
# ---------------------------------------------------------------------------


class CoachMemoryResponse(BaseModel):
    memory: str


class CoachMemoryRequest(BaseModel):
    memory: str


# ---------------------------------------------------------------------------
# AI endpoints
# ---------------------------------------------------------------------------


class StravaActivitySchema(CamelModel):
    """Mirrors the TypeScript StravaActivity interface."""

    id: int
    name: str
    type: str
    distance: float
    moving_time: int
    elapsed_time: int
    total_elevation_gain: float
    start_date: str
    average_watts: Optional[float] = None
    weighted_average_watts: Optional[float] = None
    max_watts: Optional[float] = None
    average_heartrate: Optional[float] = None
    max_heartrate: Optional[float] = None


class UserProfileSchema(CamelModel):
    """Mirrors the TypeScript UserProfile interface."""

    name: str
    email: str
    bike_type: str
    training_goal: str
    race_date: Optional[str] = None
    race_description: Optional[str] = None
    weekly_hours: Optional[float] = None
    follows_training_plan: bool = False
    resting_heart_rate: Optional[int] = None
    max_heart_rate: Optional[int] = None
    threshold_heart_rate: Optional[int] = None
    current_ftp: Optional[int] = None
    fitness_level: str


class AnalyseActivitiesRequest(CamelModel):
    activities: list[StravaActivitySchema]
    max_heart_rate: Optional[int] = None


class GeneratePlanRequest(CamelModel):
    pass


class AdaptPlanRequest(CamelModel):
    recent_feedback: list[WorkoutFeedbackSchema]


class AskTrainerRequest(CamelModel):
    question: str
    context_workout: Optional[Any] = None


class PlanDayUpdateSchema(CamelModel):
    date: str
    workout_type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    target_power: Optional[Any] = None
    target_heart_rate: Optional[Any] = None
    intervals: Optional[list[Any]] = None
    workout_purpose: Optional[str] = None
    key_focus_points: Optional[list[str]] = None


class AnalyseActivitiesResponse(CamelModel):
    """Response for the /ai/analyse-activities endpoint.

    Wraps the rider assessment together with optional plan updates so the
    frontend can apply targeted training-plan changes immediately after a
    new ride is analysed.
    """

    assessment: RiderAssessmentSchema
    plan_updates: Optional[list[PlanDayUpdateSchema]] = None


class AskTrainerResponse(CamelModel):
    response: str
    plan_updates: Optional[list[PlanDayUpdateSchema]] = None
    sources: Optional[list[Any]] = None


class TrainingDaySchema(CamelModel):
    """Enough structure to pass to rateCompletedWorkout; rest stored as opaque JSON."""

    date: str
    workout_type: str
    title: str
    description: str
    duration_minutes: int
    target_power: Optional[Any] = None
    target_heart_rate: Optional[Any] = None
    intervals: Optional[list[Any]] = None
    completed: Optional[bool] = None
    feedback: Optional[WorkoutFeedbackSchema] = None
    coach_feedback: Optional[str] = None
    workout_purpose: Optional[str] = None
    key_focus_points: Optional[list[str]] = None


class RateWorkoutRequest(CamelModel):
    day: TrainingDaySchema
    strava_activity_id: Optional[int] = None


class RateWorkoutResponse(BaseModel):
    feedback: str
    flag_for_adaptation: bool = False


class RefreshKnowledgeResponse(BaseModel):
    status: str
    message: str


class RefreshLoginSummaryResponse(CamelModel):
    login_summary: str




# ---------------------------------------------------------------------------
# Athlete metric history
# ---------------------------------------------------------------------------


class AthleteMetricSnapshotSchema(CamelModel):
    recorded_at: str
    ftp: Optional[int] = None
    threshold_hr: Optional[int] = None
    ctl: Optional[float] = None
    atl: Optional[float] = None
    tsb: Optional[float] = None
    source: str = "strava_analysis"


class MetricsHistoryResponse(BaseModel):
    snapshots: list[AthleteMetricSnapshotSchema]


class ReadinessScoreResponse(BaseModel):
    """Response for the GET /ai/readiness-score endpoint."""

    score: float
    """Combined readiness score (0–100). Blends form (TSB) and fitness (CTL)."""
    form_score: float
    """Form component of the score (0–100). Peaks at TSB +5 to +15."""
    fitness_score: float
    """Fitness component of the score (0–100). Based on CTL (42-day load)."""
    ctl: float
    """Chronic Training Load — 42-day exponential weighted average of daily TSS."""
    atl: float
    """Acute Training Load — 7-day exponential weighted average of daily TSS."""
    tsb: float
    """Training Stress Balance — CTL minus ATL (form/freshness indicator)."""
    days_until_race: int
    """Calendar days remaining until race_date (0 when race day or past)."""
    race_date: Optional[str] = None
    """ISO date string of the upcoming race, or None when not set."""
    projected_score: Optional[float] = None
    """Readiness score projected at race day using the current plan."""
    projected_ctl: Optional[float] = None
    """Projected CTL at race day."""
    projected_atl: Optional[float] = None
    """Projected ATL at race day."""
    projected_tsb: Optional[float] = None
    """Projected TSB at race day."""


# ---------------------------------------------------------------------------
# .fit file upload
# ---------------------------------------------------------------------------


class FitUploadResponse(BaseModel):
    status: str
    activity_id: str
    sport_type: str
    duration_minutes: int
    average_power: Optional[int] = None
    average_heart_rate: Optional[int] = None


# ---------------------------------------------------------------------------
# Ride metrics
# ---------------------------------------------------------------------------


class RideMetricSchema(CamelModel):
    strava_activity_id: int
    activity_date: str
    sport_type: str
    duration_seconds: Optional[int] = None
    avg_power_w: Optional[int] = None
    normalized_power_w: Optional[int] = None
    intensity_factor: Optional[float] = None
    tss: Optional[float] = None
    ftp_used: Optional[int] = None
    ctl_after: Optional[float] = None
    atl_after: Optional[float] = None
    tsb_after: Optional[float] = None
    ride_purpose: Optional[str] = None
    summary: Optional[str] = None
    coach_note: Optional[str] = None
    user_note: Optional[str] = None


class ImportHistoryResponse(BaseModel):
    processed: int
    skipped: int


class ImportProgressResponse(BaseModel):
    status: str = "idle"  # idle | running | done | error
    total: int = 0
    processed: int = 0
    skipped: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# FTP recalculation
# ---------------------------------------------------------------------------


class RecalculateMetricsRequest(CamelModel):
    """Request body for POST /users/me/recalculate-metrics."""

    ftp_override: Optional[int] = None
    """New FTP value in watts.  When provided, the user's ``current_ftp`` is
    updated before recomputing all ride metrics.  When omitted the existing
    FTP stored on the user profile is used."""


class RecalculateMetricsResponse(BaseModel):
    """Response for POST /users/me/recalculate-metrics."""

    updated: int
    """Number of ride-metric rows that were recomputed."""
    ftp_used: int
    """The FTP value (watts) that was used for all calculations."""
