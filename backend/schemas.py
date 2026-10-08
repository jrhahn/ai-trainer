"""Pydantic request/response schemas.

All schemas that are exchanged with the TypeScript frontend use camelCase
field names via a custom alias generator that preserves acronyms (FTP, HR).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import ClassVar, TYPE_CHECKING, Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_validator,
    model_serializer,
    model_validator,
)

from services import ride_purpose_question, run_model
from services.activity_identity import (
    CYCLING_SPORT_TYPES,
    SPORT_CYCLING,
    training_sport,
)

if TYPE_CHECKING:
    import models


# ---------------------------------------------------------------------------
# Alias generator: snake_case → camelCase, preserving FTP / HR as all-caps
# ---------------------------------------------------------------------------

_UPPER_ACRONYMS = {"ftp", "hr"}


# ---------------------------------------------------------------------------
# Physiological bounds for athlete-entered metrics
# ---------------------------------------------------------------------------

# Outer limits, not expectations: they exist to reject typos (a 1500 W FTP, a
# 40 bpm max HR) before the value reaches zone maths or the coach prompt, while
# staying well clear of any real athlete.  Relative plausibility — FTP against
# maximal aerobic power, resting against max HR — is checked separately.
FTP_MIN_WATTS = 30
FTP_MAX_WATTS = 700
MAX_HR_MIN_BPM = 120
MAX_HR_MAX_BPM = 230
RESTING_HR_MIN_BPM = 25
RESTING_HR_MAX_BPM = 120
# Running threshold pace, seconds per kilometre (#716). 2:00/km is faster than
# the world record for any distance beyond 800 m and 15:00/km is slower than
# walking, so anything outside is a typo or a unit mix-up — most likely a pace
# per mile pasted into a per-kilometre field.
THRESHOLD_PACE_MIN_SECONDS_PER_KM = 120.0
THRESHOLD_PACE_MAX_SECONDS_PER_KM = 900.0


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


class CaptchaChallengeResponse(BaseModel):
    """A proof-of-work challenge for the registration form (#686)."""

    algorithm: str
    challenge: str
    salt: str
    signature: str
    maxnumber: int


class CaptchaSolution(BaseModel):
    """The solved challenge, echoed back with the number the client found."""

    challenge: str
    salt: str
    signature: str
    number: int


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    captcha: CaptchaSolution | None = None
    """Optional in the schema, required by the route when captcha is enabled.

    Kept optional here so a deployment with ``CAPTCHA_ENABLED=false`` accepts
    the plain body, and so a missing solution fails with the route's own 400 —
    which says what to do about it — rather than a 422 listing a field the
    caller has never heard of.
    """

    @model_validator(mode="after")
    def password_strength(self) -> "RegisterRequest":
        password = self.password
        password_lower = password.lower()
        failures: list[str] = []

        if len(password) < 8:
            failures.append("be at least 8 characters")
        if not re.search(r"[a-z]", password):
            failures.append("include a lowercase letter")
        if not re.search(r"[A-Z]", password):
            failures.append("include an uppercase letter")
        if not re.search(r"\d", password):
            failures.append("include a number")
        if not re.search(r"[^A-Za-z0-9]", password):
            failures.append("include a special character")

        weak_terms = ("password", "qwerty", "admin", "welcome", "trainlikea")
        if any(term in password_lower for term in weak_terms):
            failures.append("avoid common or app-related words")

        personal_fragments = _password_personal_fragments(str(self.email), self.name)
        if any(fragment in password_lower for fragment in personal_fragments):
            failures.append("not include your name or email")

        if failures:
            raise ValueError(f"Password must {', '.join(failures)}.")
        return self


def _password_personal_fragments(email: str, name: str) -> set[str]:
    fragments: set[str] = set()
    local_part, _, domain = email.lower().partition("@")
    candidates = [local_part, *re.split(r"[^a-z0-9]+", local_part)]
    candidates.extend(re.split(r"[^a-z0-9]+", domain))
    candidates.extend(re.split(r"[^a-z0-9]+", name.lower()))
    return {candidate for candidate in candidates if len(candidate) >= 3}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TotpLoginRequest(CamelModel):
    """Second step of a login that needs a code (#688).

    CamelModel rather than BaseModel, and that is load-bearing: the client
    sends ``rememberDevice``. On a plain BaseModel that key does not map to
    ``remember_device``, so it is dropped and the field silently keeps its
    default — the device is never trusted and nothing reports why.
    """

    challenge: str
    code: str
    remember_device: bool = False


class LoginChallengeResponse(CamelModel):
    """What ``/auth/login`` returns instead of a token when TOTP is on.

    A 200 with a discriminated body rather than a 401: the password *was*
    accepted, and reporting that as an auth failure would leave the client
    unable to tell "wrong password" from "now show the code field".
    """

    mfa_required: bool = True
    challenge: str



class TotpEnrollResponse(CamelModel):
    """The QR and the secret, returned once when enrollment starts."""

    secret: str
    """Grouped for transcription, for anyone entering it by hand."""
    provisioning_uri: str
    qr_svg: str



class TotpConfirmRequest(BaseModel):
    code: str


class TotpStatusResponse(CamelModel):
    enabled: bool
    confirmed_at: datetime | None = None
    recovery_codes_remaining: int = 0
    trusted_device_count: int = 0



class TotpRecoveryCodesResponse(CamelModel):
    """Shown exactly once, at confirmation. Never retrievable afterwards."""

    recovery_codes: list[str]



class TotpDisableRequest(BaseModel):
    """Disabling needs the password again, not just a live session.

    A borrowed unlocked browser should not be enough to remove the factor that
    protects the account.
    """

    password: str


class SessionRevokeRequest(BaseModel):
    """Signing out everywhere needs the password, for the same reason as above.

    It is also the one request that deliberately invalidates the token making
    it: whoever presses this wants every session gone, including this one
    (#704).
    """

    password: str


class AccountDeleteRequest(BaseModel):
    """Deleting the account needs the password, like disabling the second factor.

    It did not, which made a borrowed unlocked browser enough to destroy somebody's
    training history (ai-trainer-ops#35). The two neighbouring step-up routes —
    ``/auth/totp/disable`` and ``/auth/sessions/revoke`` — both ask, on the
    argument that locking the owner out of their own account must cost more than
    a live session. Deletion is that argument's stronger case, and it is the one
    route that had no way to make it: ``require_password`` was private to
    ``auth_router``.
    """

    password: str


class SessionRevokeResponse(CamelModel):
    """What the client needs to know: the session it holds is finished.

    ``token_generation`` is returned because it is the only visible evidence
    the revocation happened, and an operator reading the admin panel or a log
    can line it up with the column.
    """

    token_generation: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Rider assessment
# ---------------------------------------------------------------------------


class RiderAssessmentSchema(CamelModel):
    rider_type: str
    notes: str
    hr_zones: Optional[Any] = None
    ride_insights: Optional[str] = None
    last_ride_feedback: Optional[str] = None
    login_summary: Optional[str] = None
    training_status_label: Optional[str] = None
    training_status_tone: Optional[str] = None
    training_status_rationale: Optional[str] = None


# ---------------------------------------------------------------------------
# User / profile
# ---------------------------------------------------------------------------


class StravaConnectionSchema(CamelModel):
    athlete_id: int
    athlete_name: str


class IntervalsConnectionSchema(CamelModel):
    athlete_id: str
    athlete_name: Optional[str] = None


class UserResponse(CamelModel):
    id: str
    email: str
    name: Optional[str] = None
    is_onboarded: bool
    strava_analysis_complete: bool
    last_strava_activity_id: Optional[int] = None
    strava_auto_sync_enabled: bool = True
    intervals_analysis_complete: bool = False
    last_intervals_activity_id: Optional[int] = None
    intervals_auto_sync_enabled: bool = True
    # profile fields
    bike_type: Optional[str] = None
    training_goal: Optional[str] = None
    race_date: Optional[str] = None
    race_description: Optional[str] = None
    weekly_hours: Optional[float] = None
    follows_training_plan: bool = False
    max_heart_rate: Optional[int] = None
    resting_heart_rate: Optional[int] = None
    current_ftp: Optional[int] = None
    threshold_pace_seconds_per_km: Optional[float] = None
    """The athlete's running threshold pace in seconds per kilometre (#716).

    What ``current_ftp`` is to the bike. ``null`` means they have not set one,
    in which case the Critical Speed fit over their run history prices their
    runs when it is confident enough — this field only ever holds what the
    athlete said."""
    fitness_level: Optional[str] = None
    ai_provider: str = "gemini"
    consumed_tokens: int = 0
    ftp_plausibility_warning: Optional[str] = None
    """Advisory message when ``current_ftp`` is implausible against the
    athlete's recorded maximal aerobic power.  ``null`` when the pair looks
    sane or no MAP reference has been recorded yet.  Never auto-corrects the
    stored FTP — the athlete owns that value."""
    # related
    rider_assessment: Optional[RiderAssessmentSchema] = None
    strava_connection: Optional[StravaConnectionSchema] = None
    intervals_connection: Optional[IntervalsConnectionSchema] = None

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
    max_heart_rate: Optional[int] = Field(
        default=None, ge=MAX_HR_MIN_BPM, le=MAX_HR_MAX_BPM
    )
    resting_heart_rate: Optional[int] = Field(
        default=None, ge=RESTING_HR_MIN_BPM, le=RESTING_HR_MAX_BPM
    )
    current_ftp: Optional[int] = Field(
        default=None, ge=FTP_MIN_WATTS, le=FTP_MAX_WATTS
    )
    threshold_pace_seconds_per_km: Optional[float] = Field(
        default=None,
        ge=THRESHOLD_PACE_MIN_SECONDS_PER_KM,
        le=THRESHOLD_PACE_MAX_SECONDS_PER_KM,
    )
    fitness_level: Optional[str] = None
    ai_provider: Optional[str] = None
    is_onboarded: Optional[bool] = None
    strava_analysis_complete: Optional[bool] = None
    last_strava_activity_id: Optional[int] = None
    strava_auto_sync_enabled: Optional[bool] = None
    intervals_analysis_complete: Optional[bool] = None
    last_intervals_activity_id: Optional[int] = None
    intervals_auto_sync_enabled: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, v: Optional[str]) -> Optional[str]:
        """Absent means unchanged; present means a name (ai-trainer-ops#40).

        Onboarding used to send the empty string it had copied before the
        profile loaded, and this field stored it over the name given at
        registration. Every account that finished onboarding lost its name.
        A blank name is never what anybody meant to save, and neither is an
        explicit ``null``: the column is nullable, so it erased the name the
        same way. The validator only runs on a value the client actually sent —
        an absent field keeps its default without coming through here.
        """
        if v is None:
            raise ValueError("name must not be null")
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("training_goal")
    @classmethod
    def supported_training_goal(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if value not in {"race", "general_fitness"}:
            raise ValueError("training_goal must be 'race' or 'general_fitness'")
        return value

    @model_validator(mode="after")
    def resting_hr_below_max_hr(self) -> "UpdateProfileRequest":
        """Reject a resting HR at or above max HR.

        Only checked when both arrive in the same request; a partial update
        that touches one of them is validated against its absolute bounds only,
        since the counterpart on the profile may itself be the stale value.
        """
        if (
            self.resting_heart_rate is not None
            and self.max_heart_rate is not None
            and self.resting_heart_rate >= self.max_heart_rate
        ):
            raise ValueError(
                "resting_heart_rate must be below max_heart_rate"
            )
        return self


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


class StrengthSetSchema(CamelModel):
    """One set of one exercise, as performed (#714).

    ``rir`` and ``rpe`` are optional *per set* because that is how an
    autoregulated session is logged: nobody annotates the warm-ups, and the
    working set is the one that carries the number. A missing ``rir`` is read as
    missing, never as "taken to failure" — see ``strength_model.effective_reps``.
    """

    exercise: str
    reps: int
    weight_kg: float
    rir: Optional[int] = None
    rpe: Optional[float] = None


class StrengthSetResultSchema(StrengthSetSchema):
    """A stored set, with the figures derived from it.

    The derived fields are computed on read by ``services.strength_model`` rather
    than stored, so correcting a formula corrects every historical figure
    instead of leaving stored values behind it.
    """

    set_index: int
    e1rm_kg: Optional[float] = None
    #: Whether the rep count sits inside the range the e1RM formulas were fitted
    #: on. A 25-rep set still yields an estimate; it is just weaker evidence, and
    #: a trend line should be able to say so.
    e1rm_confident: bool = True
    relative_intensity_pct: Optional[float] = None


class WorkoutLogRequest(CamelModel):
    """The workout log a client submits.

    ``CamelModel`` since #714 — it was a bare ``BaseModel``, which was invisible
    until a field had more than one word in its name: ``feedback`` and ``slot``
    camelise to themselves, so nothing noticed. ``populate_by_name`` means both
    spellings are still accepted, so no existing client changes.
    """

    feedback: WorkoutFeedbackSchema
    # Which session on the logged date this feedback is for (#496). Absent from
    # every pre-two-a-day client and from single-session days, meaning slot 0.
    slot: Optional[int] = None
    # Which sport the session was (#714). Absent from every existing client, in
    # which case it is read off the plan day for this (date, slot) — the sport the
    # session was *prescribed* in (#710) — and only then falls back to cycling.
    # Before this, every logged session was stored as cycling, including the gym
    # ones, which is also why their reported effort could not price them.
    sport: Optional[str] = None
    # The sets, when this was a gym session. Part of the workout log rather than
    # a separate endpoint because a gym session *is* a workout log — duration,
    # session-RPE and notes — plus its sets, and splitting them would put the
    # session's identity in two places.
    strength_sets: Optional[list[StrengthSetSchema]] = None


class StrengthSessionResponse(CamelModel):
    date: str
    slot: int
    sets: list[StrengthSetResultSchema]
    #: Σ reps × kg across every set. Zero for a session with no sets, which is a
    #: true statement about work moved rather than a missing measurement.
    volume_load_kg: float
    #: Best estimate per exercise from this session's own sets.
    best_e1rm_kg: dict[str, float]


class E1rmPointSchema(CamelModel):
    date: str
    e1rm_kg: float
    confident: bool
    #: The set the estimate came from, so a surprising point can be explained
    #: rather than only shown.
    reps: int
    weight_kg: float
    rir: Optional[int] = None


class E1rmTrendResponse(CamelModel):
    exercise: str
    formula: str
    points: list[E1rmPointSchema]


class LoggedExercisesResponse(CamelModel):
    exercises: list[str]


# ---------------------------------------------------------------------------
# Race events
# ---------------------------------------------------------------------------


class RaceEventRequest(CamelModel):
    date: str
    start_time: Optional[str] = None
    distance_km: float
    elevation_m: int

    @field_validator("date")
    @classmethod
    def date_must_be_iso(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("date must be YYYY-MM-DD")
        return value

    @field_validator("start_time")
    @classmethod
    def time_must_be_optional_hhmm(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("startTime must be HH:MM")
        return value

    @field_validator("distance_km")
    @classmethod
    def distance_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("distanceKm must be greater than 0")
        return value

    @field_validator("elevation_m")
    @classmethod
    def elevation_must_be_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("elevationM must be 0 or greater")
        return value


class RaceEventResponse(CamelModel):
    id: str
    date: str
    start_time: Optional[str] = None
    distance_km: float
    elevation_m: int

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class RaceEventsResponse(CamelModel):
    events: list[RaceEventResponse]


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


MotivationSource = Literal["inferred", "user_set"]
MotivationEntryStatus = Literal["active", "contradicted", "retired"]
MotivationComponent = Literal[
    "enjoyment", "adaptation", "consistency", "health", "race_performance"
]


class MotivationEntrySchema(CamelModel):
    """One secondary objective or constraint, with the evidence behind it (#562).

    The provenance fields are not decoration: the athlete can only correct an
    inferred objective they can see the reason for (#567), and #566 needs to know
    how long an objective has been standing before it moves a weight.
    """

    text: str
    confidence: float = 0.35
    source: MotivationSource = "inferred"
    source_snippet: str = ""
    status: MotivationEntryStatus = "active"
    contradiction_note: str | None = None
    first_observed_at: str | None = None
    last_confirmed_at: str | None = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteMotivationModelSchema(CamelModel):
    """What the athlete is optimizing for (#562).

    A *preference* ("likes MTB") lives in :class:`AthleteMemoryFactSchema`. This
    is the *objective* ("uses fitness to maximize enjoyable technical trail
    riding") — what every planning decision is scored against (#564).
    """

    primary_objective: str = ""
    primary_objective_source: MotivationSource = "inferred"
    primary_objective_confidence: float = 0.0
    primary_objective_snippet: str = ""
    secondary_objectives: list[MotivationEntrySchema] = Field(default_factory=list)
    constraints: list[MotivationEntrySchema] = Field(default_factory=list)
    # Spans exactly MotivationComponent and sums to 1.0 — guaranteed by the
    # persist gate in services.motivation_model, not re-checked by readers.
    utility_weights: dict[str, float] = Field(default_factory=dict)
    pinned_weights: list[MotivationComponent] = Field(default_factory=list)
    # modality -> affinity in [0, 1]. Independent scores, not a distribution —
    # what the weights cannot express: how the athlete wants it delivered (#564).
    modality_affinity: dict[str, float] = Field(default_factory=dict)
    updated_at: datetime | None = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteMotivationModelRequest(CamelModel):
    """An athlete's own edit of their motivation model (#567).

    Every field is optional so a partial edit is a valid request; whatever is
    sent is stored as ``user_set`` and is protected from later inference passes.
    """

    primary_objective: str | None = None
    secondary_objectives: list[MotivationEntrySchema] | None = None
    constraints: list[MotivationEntrySchema] | None = None
    utility_weights: dict[str, float] | None = None
    pinned_weights: list[MotivationComponent] | None = None
    modality_affinity: dict[str, float] | None = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class MotivationWeightEventSchema(CamelModel):
    """One recorded change to the utility weight vector (#566).

    The weights are visible to the athlete and decide what the planner
    recommends, so a change they did not make has to be able to account for
    itself. ``rules`` and ``evidence`` are absent for an athlete's own edit:
    nothing was inferred, so there is nothing to justify.
    """

    id: str
    source: MotivationSource
    weights_before: dict[str, float] = Field(default_factory=dict)
    weights_after: dict[str, float] = Field(default_factory=dict)
    # Only the components that actually moved, signed.
    deltas: dict[str, float] = Field(default_factory=dict)
    rules: list[dict[str, Any]] | None = None
    evidence: dict[str, Any] | None = None
    pinned: list[str] = Field(default_factory=list)
    recorded_at: datetime | None = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteModelSchema(CamelModel):
    """The long-term structured athlete model (#384).

    Captures durable physiological/performance characteristics. Quantitative
    anchors (``ftp_watts``, ``vo2max``) are optional; the remaining qualitative
    fields default to empty so a never-derived model round-trips as blanks.
    """

    ftp_watts: Optional[int] = None
    vo2max: Optional[float] = None
    pacing_quality: str = ""
    recovery_ability: str = ""
    threshold_durability: str = ""
    heat_tolerance: str = ""
    preferred_training_style: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    summary: str = ""
    confidence: float = 0.0
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteModelRequest(CamelModel):
    """Athlete-authored edits to the long-term model.

    Excludes ``confidence`` and ``updated_at`` — those are coach/server owned.
    """

    ftp_watts: Optional[int] = None
    vo2max: Optional[float] = None
    pacing_quality: str = ""
    recovery_ability: str = ""
    threshold_durability: str = ""
    heat_tolerance: str = ""
    preferred_training_style: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    summary: str = ""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthletePerformanceAttributeSchema(CamelModel):
    """One inferred physiological attribute in the Athlete Performance Model (#475).

    Every attribute is emitted with a ``confidence`` and never presented as fact:
    quantitative attributes carry an ``estimate`` (+ ``unit``); qualitative ones
    carry a ``score`` (e.g. ``high``/``above_average``/``unknown``). ``evidence``
    lists the signals behind it and ``missing_information`` what would sharpen it.

    ``estimate_low``/``estimate_high`` bound the estimate when the evidence
    supports a range rather than a point, and ``validation_protocol`` carries the
    concrete test that would settle it — offered instead of asserting a number the
    data does not pin down (#604). All three are absent on attributes that are
    qualitative or simply known.
    """

    estimate: Optional[float] = None
    estimate_low: Optional[float] = None
    estimate_high: Optional[float] = None
    score: Optional[str] = None
    confidence: float
    unit: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    validation_protocol: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthletePerformanceLimiterSchema(CamelModel):
    """One candidate physiological limiter in the ranked list (#477).

    Surfaced as an inference, never a fact: it carries a ``confidence`` and both
    the ``evidence`` for and the ``counterEvidence`` against it. ``limiter`` is one
    of ``threshold``/``vo2max``/``endurance_durability``/``insufficient_data``.
    """

    limiter: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingRoiSystemGainSchema(CamelModel):
    """Expected training return for one physiological system (#478).

    ``system`` is one of ``threshold``/``vo2max``/``endurance``/``anaerobic``;
    ``gain`` is a coarse return bucket (``large``/``moderate``/``small``/
    ``maintenance``) with a short ``rationale``.
    """

    system: str
    gain: str
    rationale: str = ""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingRoiEmphasisSchema(CamelModel):
    """A suggested weekly emphasis line, e.g. ``2× Threshold`` (#478)."""

    system: str
    label: str
    sessions: int

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingUtilityOptionSchema(CamelModel):
    """One (system, modality) option, scored by expected athlete utility (#564).

    Both sub-scores are carried, not just the total: a recommendation that cannot
    say how much of itself was physiology and how much was preference is not
    reviewable — by the athlete, by the coach explaining it (#565), or by whoever
    has to debug why it changed.
    """

    system: str
    modality: str
    gain: str = ""
    physiological_score: float = 0.0
    motivation_score: float = 0.0
    utility: float = 0.0
    components: dict[str, float] = Field(default_factory=dict)
    excluded_by: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingUtilitySchema(CamelModel):
    """The utility ranking behind a recommendation (#564).

    ``weights`` is recorded rather than recomputed on read: a recommendation
    whose weights are not stored cannot be audited when the ranking changes next
    month.
    """

    options: list[TrainingUtilityOptionSchema] = Field(default_factory=list)
    excluded: list[TrainingUtilityOptionSchema] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    modality_affinity: dict[str, float] = Field(default_factory=dict)

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class TrainingRoiRecommendationSchema(CamelModel):
    """ROI-based recommendation derived from the model + limiter (#478).

    Machine-readable expected gain per system plus a suggested weekly emphasis and
    a natural-language rationale that cites the model. ``sufficient`` is ``False``
    when the model has no confident limiter — the caller should keep its own
    periodization rather than act on this.
    """

    sufficient: bool = False
    limiter: Optional[str] = None
    confidence: float = 0.0
    hypothesis: str = ""
    rationale: str = ""
    expected_gain: list[TrainingRoiSystemGainSchema] = Field(default_factory=list)
    weekly_emphasis: list[TrainingRoiEmphasisSchema] = Field(default_factory=list)
    # Present only when a motivation model was supplied (#564); absent means the
    # recommendation is physiology-only, exactly as it was before.
    utility: Optional[TrainingUtilitySchema] = None

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthletePerformanceModelSchema(CamelModel):
    """Deterministic, per-attribute Athlete Performance Model (#475).

    Complements :class:`AthleteModelSchema` (LLM qualitative profile) with a
    rule-based, evidence-backed quantitative model. ``attributes`` is keyed by
    attribute name (``vo2max``, ``ftp``, ``map``, ``fractional_utilization``,
    ``aerobic_endurance``, ``fatigue_resistance``, ``anaerobic_capacity``, …).
    ``likelyLimiter`` and the ranked ``limiters`` list come from limiter
    detection (#477); ``recommendations`` is the ROI mapping derived from them
    (#478).
    """

    attributes: dict[str, AthletePerformanceAttributeSchema] = Field(
        default_factory=dict
    )
    likely_limiter: Optional[str] = None
    limiters: list[AthletePerformanceLimiterSchema] = Field(default_factory=list)
    recommendations: Optional[TrainingRoiRecommendationSchema] = None
    source_window_days: Optional[int] = None
    derived_from_rides: int = 0
    updated_at: Optional[datetime] = None

    @field_validator("limiters", mode="before")
    @classmethod
    def _limiters_default(cls, v: object) -> object:
        # The DB column is nullable (unset before limiter detection ran, or on
        # rows predating the migration); present it as an empty list.
        return v if v is not None else []

    @model_validator(mode="after")
    def _derive_recommendations(self) -> "AthletePerformanceModelSchema":
        # The ROI recommendation (#478) is a pure derivation of attributes + the
        # ranked limiter list, so compute it here rather than persist it. Imported
        # lazily to keep schemas free of a service dependency at import time.
        if self.recommendations is None:
            from services.roi_recommendation import recommend_training_roi

            attrs = {k: v.model_dump() for k, v in self.attributes.items()}
            limiters = [lim.model_dump() for lim in self.limiters]
            self.recommendations = TrainingRoiRecommendationSchema.model_validate(
                recommend_training_roi(attrs, limiters)
            )
        return self

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


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


AthleteHypothesisStatus = Literal["proposed", "confirmed", "refuted"]


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

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AthleteInquiriesResponse(CamelModel):
    inquiries: list[AthleteInquirySchema]


class AthleteInquiryAnswerRequest(CamelModel):
    answer: str = Field(min_length=1)


class AthleteInquiryAnswerResponse(CamelModel):
    """The inquiry after the answer was judged, plus what the coach said back.

    ``accepted`` reports whether the answer resolved the question. When it did
    not, ``coach_reply`` carries either the rephrased ask or the hand-off to
    Settings, so the chat can show the coach's words without a second round trip.
    """

    inquiry: AthleteInquirySchema
    accepted: bool
    coach_reply: str = ""


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


class AthleteAvailabilityConstraintSchema(CamelModel):
    id: str
    constraint_type: str
    constraint_date: Optional[str] = None
    weekday: Optional[str] = None
    reason: str = ""
    source: str = ""
    active: bool
    expires_on: Optional[str] = None
    required_workout: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


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


class AIKeyStatusSchema(CamelModel):
    provider: str
    has_openai_key: bool
    has_gemini_key: bool
    openai_model: str
    gemini_model: str
    """The model each provider actually runs, so the UI stops guessing (#691).

    The settings page used to hard-code these as display hints, in two places
    that disagreed with each other ("Gemini 2.0 Flash" and "Gemini 2.5 Flash")
    and with the backend, which has been on ``gemini-3.5-flash-lite`` since
    #511. A copy of a config value drifts from it; serving the value does not.

    This is the *coach* model specifically. Tasks are configured separately
    (classify, plan, coach, feedback), but the coach is the one the athlete
    converses with, so it is the honest answer to "what runs if I pick this".
    """


class AIKeySaveRequest(CamelModel):
    provider: str
    api_key: str = Field(min_length=1)


class ExtractAthleteFactsRequest(CamelModel):
    transcript: str = Field(min_length=1)


class AthleteFactCandidateSchema(CamelModel):
    fact: str
    category: str = "general"
    confidence: float = 0.35
    source_snippet: str = ""


class ExtractAthleteFactsResponse(CamelModel):
    candidates: list[AthleteFactCandidateSchema]


# ---------------------------------------------------------------------------
# AI endpoints
# ---------------------------------------------------------------------------

def _normalise_strava_sport_type(value: Optional[str]) -> Optional[str]:
    """Collapse every provider name for a bike ride onto ``"cycling"``.

    The vocabulary is shared with ``activity_identity.power_model_applies``,
    which has to recognise the same set: a handcycle is a bike ride whether it
    is being normalised at the import boundary or asked whether watts mean
    anything for it.
    """
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    if stripped.replace("_", "").replace("-", "").lower() in CYCLING_SPORT_TYPES:
        return "cycling"
    return stripped


# What a plan day prescribes when it says nothing: this product's sport, and the
# sport every plan written before #710 was implicitly about.
DEFAULT_PLAN_SPORT = SPORT_CYCLING


def normalise_plan_sport(value: Any) -> str:
    """The sport a plan day prescribes, in the shared sport vocabulary.

    Total by design, like :func:`normalize_slot`: a sport is part of a session's
    meaning, so an unreadable one must never drop the day at the persist gate. An
    unknown or missing sport is the cycling session this product has always
    assumed. A sport outside :data:`activity_identity.PLANNABLE_SPORTS` is kept
    as its family token rather than coerced to cycling — the planner cannot write
    one, but an athlete's own edit must not be silently relabelled.
    """
    if not isinstance(value, str):
        # Only a string can name a sport. Stringifying anything else produced
        # real-looking sports out of structured junk ("{'a': 1}" → "a1").
        return DEFAULT_PLAN_SPORT
    return training_sport(value) or DEFAULT_PLAN_SPORT


def day_sport(day: Any) -> str:
    """The sport of ``day``, accepting a ``PlanDay``, dict or alias case.

    Plans written before #710 carry no sport at all. A ``workoutType`` of
    ``"strength"`` on such a day is the one case where the *old* vocabulary
    already stated a sport, so it is honoured rather than read as cycling —
    otherwise every historical gym session would start matching bike rides.
    """
    if isinstance(day, PlanDay):
        return day.sport
    if isinstance(day, dict):
        raw = day.get("sport")
        if raw is None:
            raw = day.get("sport_type") or day.get("sportType")
        if raw is None:
            workout_type = day.get("workoutType") or day.get("workout_type")
            # Only the literal "strength" carried sport meaning in the old
            # vocabulary; every other workout type ("endurance", "intervals",
            # "rest", …) says what the session is for and nothing about which
            # sport it is.
            if str(workout_type or "").strip().lower() != "strength":
                return DEFAULT_PLAN_SPORT
            raw = workout_type
        return normalise_plan_sport(raw)
    return normalise_plan_sport(getattr(day, "sport", None))


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


class UserProfileSchema(CamelModel):
    """Mirrors the TypeScript UserProfile interface."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )

    name: str
    email: str
    bike_type: str
    training_goal: str
    race_date: Optional[str] = None
    race_description: Optional[str] = None
    weekly_hours: Optional[float] = None
    follows_training_plan: bool = False
    max_heart_rate: Optional[int] = None
    resting_heart_rate: Optional[int] = None
    current_ftp: Optional[int] = None
    threshold_pace: Optional[str] = None
    """The athlete's running threshold pace, already rendered as ``"4:10 /km"``.

    A string where ``UserResponse`` carries the number, because this schema's
    only consumers are prompt payloads: every call site dumps it into JSON the
    coach reads, and a bare ``250`` there is the unlabelled figure #467 and #716
    both forbid. ``None`` when the athlete has not set a threshold pace."""
    fitness_level: str

    @classmethod
    def from_user(cls, user: "models.User") -> "UserProfileSchema":
        """Build a ``UserProfileSchema`` from an ORM ``User`` instance."""
        return cls(
            name=user.name or "",
            email=user.email,
            bike_type=user.bike_type or "",
            training_goal=user.training_goal or "",
            race_date=user.race_date,
            race_description=user.race_description,
            weekly_hours=user.weekly_hours,
            follows_training_plan=user.follows_training_plan,
            max_heart_rate=user.max_heart_rate,
            resting_heart_rate=user.resting_heart_rate,
            current_ftp=user.current_ftp,
            threshold_pace=run_model.format_pace(
                run_model.speed_from_pace_seconds(user.threshold_pace_seconds_per_km)
            ),
            fitness_level=user.fitness_level or "",
        )


class AnalyseActivitiesRequest(CamelModel):
    activities: list[StravaActivitySchema]
    max_heart_rate: Optional[int] = None
    current_ftp: Optional[int] = None
    source: Literal["strava", "intervals"] = "strava"


class GeneratePlanRequest(CamelModel):
    pass


class AskTrainerRequest(CamelModel):
    question: str
    context_workout: Optional[Any] = None


class PlanDayUpdateSchema(CamelModel):
    date: str
    # Which session on ``date`` this update targets (#496). ``None`` means the
    # day's first (lowest-slot) session, which is what every pre-two-a-day caller
    # and every single-session day resolves to.
    slot: Optional[int] = None
    # Which sport the session prescribes (#710). ``None`` leaves the day's
    # current sport alone, so no existing caller changes it by omission.
    sport: Optional[str] = None
    time_of_day: Optional[str] = None
    workout_type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    # Optional planned-duration window (#368). A single value is the degenerate
    # window min == max; when both are set, duration_minutes is the midpoint.
    duration_min_minutes: Optional[int] = None
    duration_max_minutes: Optional[int] = None
    target_power: Optional[Any] = None
    target_heart_rate: Optional[Any] = None
    intervals: Optional[list[Any]] = None
    # The prescribed lifts, when the coach is writing a gym session (#714).
    # ``None`` leaves the day's current prescription alone, so no existing caller
    # clears one by omission — the same convention as ``sport`` above.
    strength_exercises: Optional[list[Any]] = None
    workout_purpose: Optional[str] = None
    key_focus_points: Optional[list[str]] = None
    # Marks the day done. Set by activity-sync when a ride auto-matches the day
    # (services/ride_matching.mark_matched_days_completed); clients mark completion
    # through the same per-day update path.
    completed: Optional[bool] = None


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


class PowerRange(CamelModel):
    low: int
    high: int


class HeartRateRange(CamelModel):
    low: int
    high: int


class PlanInterval(CamelModel):
    duration: int  # seconds
    power: int
    rest: int  # seconds


def _coerce_target_range(value: Any) -> Any:
    """Lenient coercion of a target power/HR value into a ``{low, high}`` range.

    The LLM occasionally emits a bare number (``"targetPower": 240``) or fills
    only one bound. Treat a scalar as a degenerate range and mirror a lone bound
    so one malformed value doesn't drop the whole day at the persist gate.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        v = int(value)
        return {"low": v, "high": v}
    if isinstance(value, str):
        try:
            v = int(float(value))
        except ValueError:
            return value
        return {"low": v, "high": v}
    if isinstance(value, dict):
        low, high = value.get("low"), value.get("high")
        if low is None and high is not None:
            return {**value, "low": high}
        if high is None and low is not None:
            return {**value, "high": low}
    return value


def normalize_slot(value: Any) -> int:
    """Coerce any stored/LLM slot value to a non-negative int, defaulting to 0.

    Total by design: a slot is a storage key, so no input may raise. Missing,
    ``None``, empty and unparseable values are all the legacy single-session day,
    i.e. slot 0; negatives are clamped so ordering stays total.

    Only genuine numbers and numeric strings are accepted. A bare ``int(value)``
    would happily convert any object defining ``__int__`` — which is how a slot
    that was never set turns into a real-looking slot 1 and silently mis-keys a
    session. Booleans are excluded for the same reason.
    """
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return 0 if value != value else max(0, int(value))  # NaN → 0
    if isinstance(value, str):
        try:
            return max(0, int(value.strip()))
        except ValueError:
            return 0
    return 0


def day_slot(day: Any) -> int:
    """The session slot of ``day``, accepting a ``PlanDay``, dict or alias case.

    Legacy days that predate two-a-days carry no slot at all and read back as 0.
    """
    if isinstance(day, PlanDay):
        return day.slot
    if isinstance(day, dict):
        raw = day.get("slot")
        if raw is None:
            raw = day.get("session_slot")
        return normalize_slot(raw)
    return normalize_slot(getattr(day, "slot", None))


def day_field(day: Any, *names: str, default: Any = None) -> Any:
    """Read one field off a plan-day **dict**, accepting either key spelling.

    ``PlanDay`` is lenient on input by design (#368, #422): a day may arrive
    camelCase from an LLM write or snake_case from a stored row, so every reader
    working on raw dicts has to try both. The widespread idiom for that was

        day.get("durationMinutes") or day.get("duration_minutes") or 0

    and it is unsound whenever the canonical value is legitimately falsy. A day
    blanked to ``durationMinutes: 0`` that still carries a stale
    ``duration_minutes: 90`` reads back as a 90-minute session, because ``0 or
    90`` is 90 — which is how a gate came to judge its own output in violation
    of the constraint it had just enforced (ai-trainer-ops#29).

    So this resolves on **presence**, in the order given, and only treats
    ``None`` as absent. ``0``, ``""`` and ``False`` are answers.

    Same rule :func:`day_slot` applies to ``slot``/``session_slot``, generalised
    to every other field — but deliberately *not* the same shape handling.
    ``day_slot`` accepts a ``PlanDay`` because its callers hold one; this takes
    dicts only, because spelling ambiguity is a property of raw dicts and a
    ``PlanDay`` has none by construction. If you hold the model, read the
    attribute. Accepting shapes no caller passes is how a reader ends up with an
    untested branch that quietly disagrees with the tested one, which is the
    class of defect this function exists to end.
    """
    if not isinstance(day, dict):
        return default
    for name in names:
        if name in day and day[name] is not None:
            return day[name]
    return default


def session_key(day: Any) -> tuple[str, int]:
    """The unique identity of a plan session: ``(date, slot)`` (#496).

    Date alone stopped being unique when a day became able to hold more than one
    session, so every map/merge/sort over a plan must key on this instead. Use it
    anywhere the old ``{d["date"]: d}`` shape appeared.
    """
    if isinstance(day, PlanDay):
        return (str(day.date), day.slot)
    if isinstance(day, dict):
        return (str(day.get("date") or ""), day_slot(day))
    return (str(getattr(day, "date", "") or ""), day_slot(day))


class StrengthPrescription(CamelModel):
    """One prescribed exercise on a planned gym session (#714).

    The intensity unit is **RIR by preference**, which is the point: the plan
    cannot know what 100 kg will feel like on a Thursday after a hard weekend,
    and "3×5 at RIR 2" is a prescription the athlete can execute honestly on a
    good day and a bad one. An absolute weight is a guess the app has no way to
    check; a percentage of e1RM is better but still assumes today's maximum
    equals last month's estimate.

    ``percent_e1rm`` is offered because it is how a block is often written, and
    because it is the unit that makes sense for an exercise the athlete has
    enough history on. Neither is required: "3×12 push-ups" is a legitimate
    prescription with no intensity unit at all.
    """

    exercise: str
    sets: int = 1
    reps: int = 1
    #: Reps in reserve. The autoregulation unit, and the one that wins when both
    #: are given — see :meth:`_one_intensity_unit`.
    rir: Optional[int] = None
    percent_e1rm: Optional[float] = None

    @field_validator("exercise", mode="before")
    @classmethod
    def _normalise_exercise(cls, value: Any) -> Any:
        # The same normalisation the *logged* sets go through, so a prescribed
        # "Back Squat" and a logged "back squat" are one exercise and the
        # prescription can be compared with what was done.
        from services.strength_model import normalize_exercise_name

        return normalize_exercise_name(value if isinstance(value, str) else None)

    @field_validator("sets", "reps", mode="before")
    @classmethod
    def _at_least_one(cls, value: Any) -> Any:
        # A prescription of zero sets is not a prescription, and an LLM that
        # omits the field must not drop the whole day at the persist gate.
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 1
        return max(1, number)

    @model_validator(mode="after")
    def _one_intensity_unit(self) -> "StrengthPrescription":
        """Keep RIR and drop the percentage when both are given.

        Normalised rather than rejected, in keeping with this gate: a day must
        never fail validation and vanish (#424). Both units together is not a
        richer prescription, it is an ambiguous one — they name different weights
        on exactly the days they disagree, which are the days autoregulation
        exists for. RIR wins because it is the unit that survives the athlete
        being tired, which is the whole argument for prescribing in it.
        """
        from services.strength_model import MAX_RIR

        if self.rir is not None and self.percent_e1rm is not None:
            self.percent_e1rm = None
        if self.rir is not None:
            # Clamped against the same ceiling the *logged* sets are, so a
            # prescription and a log cannot disagree about what RIR 12 means.
            self.rir = max(0, min(int(self.rir), MAX_RIR))
        if self.percent_e1rm is not None and not 0 < self.percent_e1rm <= 150:
            # Outside this a percentage is a typo, not a prescription — 800 % of
            # an e1RM is not a weight anyone can lift, and 0 % is not a set.
            self.percent_e1rm = None
        return self


class PlanDay(CamelModel):
    """Canonical, self-normalizing training-plan *session*.

    Every plan write is validated and dumped through this model at the pipeline
    persist gate (``services/plan_pipeline.py``), so a day can never reach
    storage with an incoherent duration (scalar ``durationMinutes`` vs a
    ``durationMin/MaxMinutes`` window) — the recurring drift-bug class
    (#368, #422). Lenient on input (LLM/legacy days may omit fields, use
    snake_case, or send a scalar-only / window-only duration), strict and
    canonical on output. ``extra="allow"`` preserves any unmodelled key so
    typing never silently drops stored data.

    A plan is a flat list of these, and a *date may repeat*: two-a-days are two
    entries sharing one date and distinguished by ``slot`` (#496). The unique
    identity of a session is therefore ``(date, slot)`` — see :func:`session_key`
    — not the date alone. Keeping the session as the model (rather than nesting
    ``sessions`` under a day container) is what lets this single persist gate go
    on owning every duration/drift invariant unchanged.
    """

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="allow",
    )

    date: str
    # Ordered position of this session within its date: 0 is the first/only
    # session, 1 the second, and so on. A legacy single-workout day carries no
    # slot and reads back as slot 0, so every stored plan migrates losslessly.
    slot: int = 0
    # Which sport this session prescribes (#710). ``workout_type`` says what the
    # session is *for* ("endurance", "intervals"); this says what the athlete
    # actually does. Defaults to cycling, so every stored plan — all of which
    # predate the field — reads back as the cycling plan it is, exactly the way
    # ``slot`` migrated in #496. Normalised into the shared sport vocabulary so a
    # planned sport and an imported activity's sport are comparable.
    sport: str = DEFAULT_PLAN_SPORT
    # Optional free-text when-in-the-day hint ("am", "pm", "18:30"). Advisory
    # only — ``slot`` is the identity and the ordering; this is for display and
    # for the coach's AM/PM load reasoning.
    time_of_day: Optional[str] = None
    workout_type: str = "rest"
    title: str = ""
    description: str = ""
    duration_minutes: int = 0
    # Optional planned-duration window (#368). A single value is the degenerate
    # window min == max; when both are set, duration_minutes is the midpoint.
    duration_min_minutes: Optional[int] = None
    duration_max_minutes: Optional[int] = None
    target_power: Optional[PowerRange] = None
    target_heart_rate: Optional[HeartRateRange] = None
    intervals: Optional[list[PlanInterval]] = None
    # What the athlete is meant to lift, when this is a gym session (#714).
    # ``intervals`` above is the cycling equivalent and is deliberately not
    # reused: an interval is a duration at a power, a set is reps at a load, and
    # one schema covering both would be a schema that describes neither.
    strength_exercises: Optional[list[StrengthPrescription]] = None
    completed: Optional[bool] = None
    feedback: Optional[WorkoutFeedbackSchema] = None
    coach_feedback: Optional[str] = None
    workout_purpose: Optional[str] = None
    key_focus_points: Optional[list[str]] = None
    # Server-authoritative marker of which trigger last set this day. "user"
    # means a manual edit / coach-chat change and pins the day against automated
    # overwrites; see services/plan_pipeline.py. Clients cannot set this.
    source: Optional[str] = None

    @field_validator("target_power", "target_heart_rate", mode="before")
    @classmethod
    def _coerce_ranges(cls, value: Any) -> Any:
        return _coerce_target_range(value)

    @field_validator("sport", mode="before")
    @classmethod
    def _coerce_sport(cls, value: Any) -> Any:
        # Same reasoning as the slot coercion below: a garbage sport must not
        # drop the day at the persist gate, and an absent one is the cycling
        # session every pre-#710 plan is.
        return normalise_plan_sport(value)

    @field_validator("strength_exercises", mode="before")
    @classmethod
    def _coerce_strength_exercises(cls, value: Any) -> Any:
        """Read what the writer meant, or nothing — but never raise (#714).

        This was the one field on the model that could still fail validation,
        and the cost was not a dropped day: ``ai_service`` *retries* on a
        ``ValidationError``, so a model that wrote ``["3x5 squat"]`` burned all
        three plan-sized attempts and the athlete got no plan at all. The
        structured prescription is a bonus on top of the session description —
        it must never be the reason a whole plan write fails.

        A bare string is read as an exercise name, which is the same information
        the dict form carries with ``sets``/``reps`` omitted, and those already
        default rather than reject. Anything genuinely unreadable is dropped,
        and a list with nothing readable left in it becomes absent rather than
        ``[]`` — an empty list would put a new key into every stored plan day
        and cost the byte-stability :meth:`_omit_storage_defaults` protects.
        """
        from services.strength_model import normalize_exercise_name

        if value is None:
            return None
        # A single prescription written without its list, which is a common
        # shape when the session has exactly one lift.
        entries = value if isinstance(value, list) else [value]

        kept: list[Any] = []
        for entry in entries:
            if isinstance(entry, str):
                entry = {"exercise": entry}
            if not isinstance(entry, dict):
                continue
            # A nameless prescription is not one: it renders as a blank row and
            # can never be matched against what the athlete actually lifted.
            if not normalize_exercise_name(
                entry.get("exercise")
                if isinstance(entry.get("exercise"), str)
                else None
            ):
                continue
            kept.append(entry)
        return kept or None

    @field_validator("slot", mode="before")
    @classmethod
    def _coerce_slot(cls, value: Any) -> Any:
        # Slot is a storage key, so it must never fail validation and drop a day
        # at the persist gate: a missing/None/garbage slot is the legacy
        # single-session day, i.e. slot 0. Negatives are clamped for the same
        # reason (ordering must stay total and non-negative).
        return normalize_slot(value)

    @model_serializer(mode="wrap")
    def _omit_storage_defaults(self, handler: Any) -> Any:
        """Serialize slot 0 and a cycling sport as absent, as they were before.

        Slot 0 *is* the legacy "no slot" day, so writing it out would rewrite every
        stored plan on the first commit after #496 ships and — worse — make a
        genuinely unchanged plan compare unequal to what is in the database, turning
        every no-op write into a real write that cascades a login-summary refresh
        and a ride-snapshot rebuild. Omitting the default keeps storage byte-stable
        and no-op detection honest; readers already default a missing slot to 0.

        ``sport`` is omitted for exactly the same reason (#710): every stored plan
        predates the field and is implicitly cycling, so emitting ``"cycling"``
        would rewrite all of them on the first commit and turn every subsequent
        no-op into a real write. :func:`day_sport` defaults a missing sport, so
        nothing downstream can tell the difference.
        """
        data = handler(self)
        if isinstance(data, dict) and data.get("slot") in (0, None):
            data.pop("slot", None)
        if isinstance(data, dict) and data.get("sport") in (DEFAULT_PLAN_SPORT, None):
            data.pop("sport", None)
        # And ``strengthExercises`` for the third time (#714). Every stored plan
        # predates the field, so emitting an explicit ``null`` would make each one
        # compare unequal to its own re-dump and rewrite the lot on the first
        # commit — the same cascade into a login-summary refresh and a
        # ride-snapshot rebuild that the two above exist to prevent.
        if isinstance(data, dict) and data.get("strengthExercises") is None:
            data.pop("strengthExercises", None)
            data.pop("strength_exercises", None)
        return data

    @model_validator(mode="after")
    def _coherent_duration(self) -> "PlanDay":
        # Reconcile the scalar with the min/max window so every reader sees one
        # coherent view. Shared arithmetic with normalize_duration_fields;
        # imported lazily because services modules import schemas at load time.
        from services.duration_range import reconcile_duration

        lo, hi = reconcile_duration(
            self.duration_minutes,
            self.duration_min_minutes,
            self.duration_max_minutes,
        )
        if lo is None and hi is None:
            # Single value / rest day — leave the scalar untouched.
            return self
        self.duration_min_minutes = lo
        self.duration_max_minutes = hi
        self.duration_minutes = round((lo + hi) / 2)
        return self


# Back-compat alias: the canonical day model used to be TrainingDaySchema.
TrainingDaySchema = PlanDay


_DURATION_ALIAS_KEYS = frozenset(
    {"durationMinutes", "durationMinMinutes", "durationMaxMinutes"}
)


def merge_update(day: PlanDay, update: "PlanDayUpdateSchema") -> PlanDay:
    """Apply a partial per-day ``update`` onto a canonical ``day``.

    Only fields the update actually sets (non-``None``) are applied. Duration is
    treated as one unit: an update touching *any* duration field drops the base
    day's other duration fields so the ``PlanDay`` validator rebuilds the trio
    from the update alone — otherwise a new scalar could be crushed back to a
    stale window's midpoint (#422). Unknown keys on the base day are preserved
    (``PlanDay`` uses ``extra="allow"``).
    """
    patch = update.model_dump(by_alias=True, exclude_none=True)
    base = day.model_dump(by_alias=True)
    if _DURATION_ALIAS_KEYS & patch.keys():
        for stale in _DURATION_ALIAS_KEYS - patch.keys():
            base.pop(stale, None)
    return PlanDay.model_validate({**base, **patch})


class AskTrainerResponse(CamelModel):
    response: str
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


# ---------------------------------------------------------------------------
# Athlete metric history
# ---------------------------------------------------------------------------


class AthleteMetricSnapshotSchema(CamelModel):
    recorded_at: str
    ftp: Optional[int] = None
    map_5min: Optional[int] = None
    """Best 5-minute power (W) at the time of the snapshot — the maximal
    aerobic power proxy.  ``null`` on snapshots recorded before this was
    tracked, or when no ride in the window contained a 5-minute effort."""
    ctl: Optional[float] = None
    atl: Optional[float] = None
    tsb: Optional[float] = None
    ctl_by_sport: Optional[dict[str, float]] = None
    """Per-sport chronic load at the time of the snapshot (#713). ``ctl`` above
    is a single sport's rung out of this; ``atl`` is the aggregate across all of
    them. ``null`` on snapshots recorded before the split, which mean cycling."""
    source: str = "strava_analysis"


class MetricsHistoryResponse(BaseModel):
    snapshots: list[AthleteMetricSnapshotSchema]


class ReasoningItem(BaseModel):
    """A single supporting-evidence bullet, tagged with its knowledge source.

    The ``source`` distinguishes where the knowledge comes from (issue #377) so
    the athlete can tell a personal observation about themselves apart from
    established sports science and from the coach's read of their metrics.
    """

    source: str
    """One of ``personal_observation``, ``scientific_evidence``, ``coach_inference``."""
    text: str
    """The reasoning bullet itself, without any source-label prefix."""


class ReadinessRecommendation(BaseModel):
    """A single readiness recommendation together with the evidence behind it.

    Each recommendation is transparent about *why* it was made: ``reasoning``
    holds short supporting-evidence bullets, each tagged with its knowledge
    source (personal observation, scientific evidence, or coach inference).
    """

    recommendation: str
    """The actionable advice, e.g. "Prioritise 2–3 easy recovery rides this week."."""
    reasoning: list[ReasoningItem] = []
    """Source-tagged supporting-evidence bullets explaining the recommendation."""


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
    recommendations: list[ReadinessRecommendation] = []
    """Actionable tips to improve race readiness, each with its supporting evidence."""


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


class FitUploadFileResult(CamelModel):
    filename: str
    status: Literal["imported", "skipped", "failed"]
    message: str
    activity_id: Optional[str] = None
    sport_type: Optional[str] = None
    duration_minutes: Optional[int] = None
    average_power: Optional[int] = None
    average_heart_rate: Optional[int] = None


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
# Weather: training location + upcoming forecast (#495)
# ---------------------------------------------------------------------------


class AthleteHomeLocationSchema(CamelModel):
    """The athlete's persisted training location.

    ``source`` is the authority marker: ``user_set`` (the athlete told the coach
    where they train) always outranks ``inferred`` (clustered ride starts) and is
    never overwritten by an inference pass.
    """

    latitude: float
    longitude: float
    label: str = ""
    source: str = "inferred"
    confidence: float = 0.0
    ride_count: int = 0
    updated_at: Optional[datetime] = None


class AthleteHomeLocationResponse(CamelModel):
    """Nullable wrapper — an athlete may not have a training location yet."""

    location: Optional[AthleteHomeLocationSchema] = None


class AthleteHomeLocationUpdate(CamelModel):
    """Athlete-supplied training location; always stored as ``user_set``."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str = ""


class DailyForecastSchema(CamelModel):
    """One day of the upcoming outlook near the athlete's training location.

    ``load_flag`` is the coaching-relevant summary (``very_hot``, ``freezing``,
    ``rain``, …) derived by :func:`services.weather_service.weather_load_flag`, so
    the UI and the plan prompts read the same judgement.
    """

    date: str
    condition: Optional[str] = None
    weather_code: Optional[int] = None
    temperature_max_c: Optional[float] = None
    temperature_min_c: Optional[float] = None
    precipitation_mm: Optional[float] = None
    wind_speed_kph: Optional[float] = None
    load_flag: Optional[str] = None


class WeatherForecastResponse(CamelModel):
    location: Optional[AthleteHomeLocationSchema] = None
    days: list[DailyForecastSchema] = []


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
    status: str = "idle"  # idle | running | done | error
    total: int = 0
    processed: int = 0
    imported: int = 0
    skipped: int = 0
    failed_activities: list[ImportFailedActivitySchema] = Field(default_factory=list)
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


# ---------------------------------------------------------------------------
# Next-ride recommendation (Task 6)
# ---------------------------------------------------------------------------


class NextRideRecommendationRequest(RideIdentityRequest):
    """Request body for POST /ai/next-ride-recommendation."""

    strava_activity_id: Optional[int] = None
    """Activity ID of the ride just reviewed.  When provided the recommendation
    is based on that specific ride; when omitted the most recently imported ride
    metric is used.

    Unlike :class:`RateWorkoutRequest`, this id *does* look one of our own rows
    up, so it is subject to the float64 identity problem: a rounded intervals id
    matches nothing and the endpoint falls through to "no ride at all" and
    recommends from thin air — quietly, which is worse in kind than the 404 the
    resolve path gave. No UI calls this today, so that is latent rather than
    observed; the inherited ``externalActivityId`` closes it before it is (#441)."""


class NextRideRecommendationResponse(CamelModel):
    """Response for POST /ai/next-ride-recommendation."""

    response: str
    """Natural coach message explaining the recommendation."""

    next_session_recommendation: str
    """Short one-sentence summary of what the athlete should do next."""

    recommendation_type: str = "keep_as_planned"
    """One of: keep_as_planned, easier, recovery, move_intensity."""

    plan_updates: Optional[list[PlanDayUpdateSchema]] = None
    """Plan changes to apply.  Present only when the next session should change."""


class ProcessPendingFeedbacksRequest(CamelModel):
    """Request body for POST /ai/process-pending-feedbacks."""

    activity_ids: list[int | str]
    """Activity IDs or external activity IDs whose feedback should be processed."""


class ProcessPendingFeedbacksResponse(CamelModel):
    """Response for POST /ai/process-pending-feedbacks."""

    login_summary: str
    """Updated training summary incorporating the batched ride feedback."""
