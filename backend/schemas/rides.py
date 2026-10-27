"""Ride metrics, .fit uploads, imports, ride feedback and the power curve."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

from domain import ride_purpose_question
from schemas.base import CamelModel
from schemas.plan_day import PlanDayUpdateSchema, _normalise_strava_sport_type
from schemas.profile import (
    MAX_HR_MAX_BPM,
    MAX_HR_MIN_BPM,
    RESTING_HR_MAX_BPM,
    RESTING_HR_MIN_BPM,
)


class StravaActivitySchema(CamelModel):
    """Mirrors the TypeScript StravaActivity interface."""

    id: int
    # Raw, non-numeric provider id (e.g. intervals.icu ``i166933341``). Carried
    # as a string so it never round-trips through a JS ``Number`` and loses
    # precision the way the numeric ``id`` does for 19-digit intervals hashes
    # (#429 Bug B). When present it is the authoritative external key on import.
    external_id: Optional[str] = None
    name: str
    type: str
    sport_type: Optional[str] = None
    distance: float
    moving_time: int
    elapsed_time: int
    total_elevation_gain: float
    start_date: str
    start_date_local: Optional[str] = None
    start_latlng: Optional[list[float]] = None
    average_watts: Optional[float] = None
    weighted_average_watts: Optional[float] = None
    max_watts: Optional[float] = None
    average_heartrate: Optional[float] = None
    max_heartrate: Optional[float] = None

    @model_validator(mode="after")
    def normalise_sport_type(self) -> "StravaActivitySchema":
        self.sport_type = _normalise_strava_sport_type(self.sport_type or self.type)
        return self


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
    # The rung the stored load came from: "power", "heart_rate", "duration"…
    # (``domain.training_load``), so an estimate can say so (ai-trainer-ops#311).
    load_source: Optional[str] = None


class FitUploadFileResult(CamelModel):
    filename: str
    status: Literal["imported", "skipped", "failed"]
    message: str
    activity_id: Optional[str] = None
    # The day the activity happened, so the result can link to it (#48).
    activity_date: Optional[str] = None
    sport_type: Optional[str] = None
    duration_minutes: Optional[int] = None
    average_power: Optional[int] = None
    average_heart_rate: Optional[int] = None
    # As on ``FitUploadResponse`` (ai-trainer-ops#311).
    load_source: Optional[str] = None


class FitBulkUploadResponse(CamelModel):
    status: str
    total: int
    imported: int
    skipped: int
    failed: int
    files: list[FitUploadFileResult]


# ---------------------------------------------------------------------------
# Ride metrics
# ---------------------------------------------------------------------------


# ``logged_sessions.LOGGED_SOURCE``; a literal because schemas must not import
# services that import schemas. Pinned equal by a test.
LOGGED_ACTIVITY_SOURCE = "logged"


class RideMetricSchema(CamelModel):
    strava_activity_id: int
    activity_source: str = "strava"
    external_activity_id: Optional[str] = None
    source_metadata: Optional[dict[str, Any]] = None
    activity_name: Optional[str] = None
    activity_start_datetime: Optional[str] = None
    activity_date: str
    sport_type: str
    duration_seconds: Optional[int] = None
    start_lat: Optional[float] = None
    start_lng: Optional[float] = None
    weather_temperature_c: Optional[float] = None
    weather_apparent_temperature_c: Optional[float] = None
    weather_condition: Optional[str] = None
    weather_code: Optional[int] = None
    weather_wind_speed_kph: Optional[float] = None
    weather_precipitation_mm: Optional[float] = None
    weather_source: Optional[str] = None
    avg_power_w: Optional[int] = None
    avg_heart_rate_bpm: Optional[int] = None
    distance_km: Optional[float] = None
    normalized_power_w: Optional[int] = None
    intensity_factor: Optional[float] = None
    tss: Optional[float] = None
    # "provider" / "power" / "heart_rate" / "duration" — the browser needs it to
    # avoid labelling an estimated load as a measured TSS (#579).
    tss_source: Optional[str] = None
    ftp_used: Optional[int] = None
    # ``ctl_after`` is this session's own sport's fitness; ``atl_after`` is the
    # athlete's aggregate fatigue across every sport, and ``ctl_by_sport`` the
    # whole ledger so a chart can draw one line per sport instead of one line
    # that changes sport whenever the athlete cross-trains (#713). NULL on rows
    # written before #713.
    ctl_after: Optional[float] = None
    atl_after: Optional[float] = None
    tsb_after: Optional[float] = None
    ctl_by_sport: Optional[dict[str, float]] = None
    ride_purpose: Optional[str] = None
    classification_confidence: Optional[str] = None
    classification_reason: Optional[str] = None
    # NULL while the "what was this session?" question is still open (#580).
    purpose_question_status: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def purpose_question_open(self) -> bool:
        """Should this activity still be asking the athlete what it was? (#580)

        Derived here rather than in the browser on purpose: whether a session
        counts as unresolved is a rule about classification confidence, and a
        copy of it in TypeScript would drift from the one the prompts and the
        persistence gate use.
        """
        # A session the athlete logged by hand has no streams for a classifier
        # to be unsure about — and they already said what it was, by entering
        # it (#47). Asking "Recovery? Tempo? VO2max?" about it is noise.
        if self.activity_source == LOGGED_ACTIVITY_SOURCE:
            return False
        return ride_purpose_question.question_is_open(
            ride_purpose=self.ride_purpose,
            classification_confidence=self.classification_confidence,
            purpose_question_status=self.purpose_question_status,
        )

    summary: Optional[str] = None
    coach_note: Optional[str] = None
    user_note: Optional[str] = None
    feel_legs: Optional[str] = None
    label_override: Optional[str] = None
    match_score: Optional[int] = None
    match_label: Optional[str] = None
    plan_match_status: str = "unmatched"
    matched_plan_date: Optional[str] = None
    matched_plan_snapshot: Optional[Any] = None
    matched_at: Optional[datetime] = None


class RideMetricHistoryResponse(BaseModel):
    rides: list[RideMetricSchema]


# ---------------------------------------------------------------------------
# Ride feedback
# ---------------------------------------------------------------------------


class RideIdentityRequest(CamelModel):
    """Base for any request that names one of the athlete's activities.

    Inherited rather than repeated, because repeating it is what went wrong. A
    non-Strava activity carries a synthesized 63-bit ``strava_activity_id``, and
    JavaScript numbers are float64: anything above 2^53 is rounded on the way
    through the browser, so the number that arrives matches no row. The
    provider's own string id is the identity that survives the trip.

    Three endpoints learned this separately, and the third learned it two weeks
    late — every resolve of an ambiguous intervals ride 404'd with "Could not
    save that" (#441, PR#588). A new endpoint that names a ride now gets the
    field by inheriting, and ``test_ride_identity_gate.py`` fails if one does
    not.

    The backend resolves it through :func:`crud.get_ride_metric_by_identity`,
    which prefers this string and keeps the numeric id as the fallback.
    """

    external_activity_id: Optional[str] = None


class RideFeedbackRequest(RideIdentityRequest):
    """Quick post-ride "how the legs felt" signal set from the dashboard.

    This is the only structured field captured by tapping an activity.  Richer
    feedback (perceived effort, free-text notes, plan-match corrections) is
    captured conversationally through the coach chat, not here.  ``legs`` may be
    ``None`` to clear a previously set value.
    """

    legs: Optional[Literal["fresh", "normal", "heavy"]] = None
    """Subjective leg-freshness rating, or ``None`` to clear it."""


class RideFeedbackResponse(CamelModel):
    """Response returned after saving ride feedback."""

    strava_activity_id: int
    ride: Optional[RideMetricSchema] = None


class RidePurposeAnswerRequest(RideIdentityRequest):
    """The athlete's answer to "what was this session?" (#580).

    ``purpose`` must be one of the classifier's own labels — an answer nothing
    downstream can read would leave the question as consequence-free as the
    prose version it replaces. ``None`` records a skip: the athlete does not
    want to say, and the question stops rather than being asked forever.
    """

    purpose: Optional[
        Literal[
            "recovery",
            "endurance",
            "tempo",
            "interval_sweetspot",
            "interval_threshold",
            "interval_vo2max",
            "interval_sprints",
            "mixed",
        ]
    ] = None
    """What the session actually was, or ``None`` to skip the question."""


class RidePurposeAnswerResponse(CamelModel):
    """The updated ride, so the card can re-render from the stored truth."""

    strava_activity_id: int
    ride: Optional[RideMetricSchema] = None


class ImportHistoryResponse(BaseModel):
    processed: int
    skipped: int


class ImportFailedActivitySchema(CamelModel):
    activity_id: Optional[int] = None
    activity_name: Optional[str] = None
    activity_date: Optional[str] = None
    reason: str


class ImportProgressResponse(CamelModel):
    job_id: Optional[str] = None
    # idle | running | analysing (the coach looks at the rides, ai-trainer-ops#306)
    # | done | error | interrupted (a restart cut it short, #284)
    status: str = "idle"
    total: int = 0
    processed: int = 0
    imported: int = 0
    skipped: int = 0
    failed_activities: list[ImportFailedActivitySchema] = Field(default_factory=list)
    error: str = ""
    # After an import that asked the coach to look (ai-trainer-ops#306): whether
    # it did, and how many days of the plan it changed. None for any other.
    analysed: Optional[bool] = None
    plan_changes: Optional[int] = None


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


# ---------------------------------------------------------------------------
# FTP estimation
# ---------------------------------------------------------------------------


class EstimateFTPRequest(CamelModel):
    """Request body for POST /users/me/estimate-ftp.

    All fields are optional.  When provided they are saved to the user
    profile so subsequent analyses (e.g. Strava import) automatically use the
    updated values.
    """

    max_heart_rate: Optional[int] = Field(
        default=None, ge=MAX_HR_MIN_BPM, le=MAX_HR_MAX_BPM
    )
    """Athlete's maximum heart rate in bpm."""

    resting_heart_rate: Optional[int] = Field(
        default=None, ge=RESTING_HR_MIN_BPM, le=RESTING_HR_MAX_BPM
    )
    """Athlete's resting heart rate in bpm."""

    @model_validator(mode="after")
    def resting_hr_below_max_hr(self) -> "EstimateFTPRequest":
        if (
            self.resting_heart_rate is not None
            and self.max_heart_rate is not None
            and self.resting_heart_rate >= self.max_heart_rate
        ):
            raise ValueError("resting_heart_rate must be below max_heart_rate")
        return self


class EstimateFTPResponse(CamelModel):
    """Response for POST /users/me/estimate-ftp."""

    estimated_ftp: Optional[int] = None
    """Best available FTP estimate in watts, or ``null`` when no data is
    available yet (e.g. brand-new account with no rides)."""

    source: str = "none"
    """Provenance of the returned estimate:
    - ``"ftp_estimation"`` – most recent snapshot from the over-time estimator.
    - ``"strava_analysis"`` – snapshot produced by a full Strava analysis.
    - ``"rider_assessment"`` – value stored in the rider assessment record.
    - ``"profile"`` – value manually set on the user profile.
    - ``"none"`` – no estimate available.
    """


# ---------------------------------------------------------------------------
# Batch ride review
# ---------------------------------------------------------------------------


class BatchReviewRidesResponse(CamelModel):
    """Response for POST /ai/review-new-rides."""

    review: str
    """Coach's batch review text covering all newly added rides."""

    ride_count: int
    """Number of rides included in this review."""


class ResolveRideMatchRequest(RideIdentityRequest):
    """Request body for POST /ai/resolve-ride-match."""

    planned_date: str
    strava_activity_id: int
    # Which session of that date the athlete meant. Ambiguity is most likely on
    # a two-a-day, and without this the resolve could only ever land on the
    # day's first session (#547). Omitted means "the only one there is", which
    # is what every single-session day sends.
    planned_slot: Optional[int] = None


class ResolveRideMatchResponse(CamelModel):
    """Response returned after resolving an ambiguous planned-workout ride."""

    ride: RideMetricSchema
    coach_note: Optional[str] = None
    plan_updates: Optional[list[PlanDayUpdateSchema]] = None


class ProcessPendingFeedbacksRequest(CamelModel):
    """Request body for POST /ai/process-pending-feedbacks."""

    activity_ids: list[int | str]
    """Activity IDs or external activity IDs whose feedback should be processed."""


class ProcessPendingFeedbacksResponse(CamelModel):
    """Response for POST /ai/process-pending-feedbacks."""

    login_summary: str
    """Updated training summary incorporating the batched ride feedback."""


class PowerDurationPointSchema(CamelModel):
    seconds: int
    watts: int


class PowerCurveBandSchema(CamelModel):
    seconds: int
    watts: int
    low: int
    high: int


class CriticalPowerSchema(CamelModel):
    cp: int
    cp_low: int
    cp_high: int
    w_prime: int
    w_prime_low: int
    w_prime_high: int
    points_used: int


class PowerCurveResponse(CamelModel):
    """The public FIT tool's answer (ai-trainer-ops#25). Nothing is stored."""

    duration_seconds: int
    points: list[PowerDurationPointSchema]
    # None when the ride has no hard short effort or too few 2-20 min points.
    model: CriticalPowerSchema | None
    curve: list[PowerCurveBandSchema]
