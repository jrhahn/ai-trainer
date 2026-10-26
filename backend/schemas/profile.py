"""The athlete's profile, the bounds its numbers must keep, and the AI key."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from domain import day_preference, run_model
from schemas.base import CamelModel, _to_camel

if TYPE_CHECKING:
    import models


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


# Body weight in kilograms (ai-trainer-ops#188), outer limits again.
BODY_WEIGHT_MIN_KG = 30.0


BODY_WEIGHT_MAX_KG = 250.0


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


_Weekday = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]


class DayPreferenceSchema(CamelModel):
    """A weekday the athlete prefers over another (ai-trainer-ops#204)."""

    prefer: _Weekday
    over: _Weekday


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
    sports: Optional[list[str]] = None
    training_days: Optional[list[str]] = None
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
    weight_kg: Optional[float] = None
    """Body weight in kilograms, ``null`` unless the athlete entered one (#188)."""
    fitness_level: Optional[str] = None
    day_preferences: list[DayPreferenceSchema] = []
    """Weekdays preferred over others, said in chat (ai-trainer-ops#204)."""
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
    # Whether this server can connect Strava at all: without its client
    # credentials the button can only fail (ai-trainer-ops#131).
    strava_available: bool = True

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# The onboarding answers (ai-trainer-ops#43): the sports the planner can plan,
# and the days of the week as lower-case English names.
OnboardingSport = Literal["cycling", "running", "strength"]


WEEK_ORDER = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def week_ordered(days: Any) -> list[str]:
    """``days`` Monday to Sunday; a name outside the week goes last, as it came.

    Onboarding stored the days in tap order ("tuesday, thursday, saturday,
    sunday, wednesday"), and every reader repeated it (ai-trainer-ops#190).
    """
    listed = [d for d in (days or []) if isinstance(d, str)]
    known = [d for d in WEEK_ORDER if d in listed]
    return known + [d for d in listed if d not in WEEK_ORDER]


TrainingWeekday = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]


def _require_iso_calendar_date(value: str) -> str:
    """YYYY-MM-DD *and* a real calendar date (#912).

    Shared by the two date inputs that reach a ``String`` column: the race
    event ``date`` and the profile ``race_date``. The regex fixes the shape,
    ``strptime`` rejects an impossible month/day like ``2027-13-40``, and the
    10-char result sits well inside the backing columns, so a nonsense or
    over-long value is a 422 here rather than a bad row or an unhandled 500.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("date must be a real calendar date") from exc
    return value


class UpdateProfileRequest(CamelModel):
    """All fields optional – PATCH-style partial update via PUT."""

    # max_length matches the backing column (models.User), so an over-long value
    # is a 422 here rather than an unhandled 500 at write time (#912).
    name: Optional[str] = Field(default=None, max_length=255)
    bike_type: Optional[str] = Field(default=None, max_length=50)
    training_goal: Optional[str] = Field(default=None, max_length=50)
    race_date: Optional[str] = None
    race_description: Optional[str] = None
    weekly_hours: Optional[float] = Field(default=None, ge=1, le=40)
    sports: Optional[list[OnboardingSport]] = Field(default=None, min_length=1)
    training_days: Optional[list[TrainingWeekday]] = Field(default=None, min_length=1)
    follows_training_plan: Optional[bool] = None
    max_heart_rate: Optional[int] = Field(
        default=None, ge=MAX_HR_MIN_BPM, le=MAX_HR_MAX_BPM
    )
    resting_heart_rate: Optional[int] = Field(
        default=None, ge=RESTING_HR_MIN_BPM, le=RESTING_HR_MAX_BPM
    )
    current_ftp: Optional[int] = Field(default=None, ge=FTP_MIN_WATTS, le=FTP_MAX_WATTS)
    threshold_pace_seconds_per_km: Optional[float] = Field(
        default=None,
        ge=THRESHOLD_PACE_MIN_SECONDS_PER_KM,
        le=THRESHOLD_PACE_MAX_SECONDS_PER_KM,
    )
    # Optional (ai-trainer-ops#188): an explicit null removes it.
    weight_kg: Optional[float] = Field(
        default=None, ge=BODY_WEIGHT_MIN_KG, le=BODY_WEIGHT_MAX_KG
    )
    fitness_level: Optional[str] = Field(default=None, max_length=50)
    ai_provider: Optional[str] = Field(default=None, max_length=20)
    is_onboarded: Optional[bool] = None
    strava_analysis_complete: Optional[bool] = None
    last_strava_activity_id: Optional[int] = None
    strava_auto_sync_enabled: Optional[bool] = None
    intervals_analysis_complete: Optional[bool] = None
    last_intervals_activity_id: Optional[int] = None
    intervals_auto_sync_enabled: Optional[bool] = None

    @field_validator("sports", "training_days")
    @classmethod
    def choices_once_each(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        """A set of choices: a repeated entry is kept once, in its first place."""
        return None if v is None else list(dict.fromkeys(v))

    @field_validator("training_days")
    @classmethod
    def days_in_week_order(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        """Monday to Sunday, not the order they were tapped (ai-trainer-ops#190)."""
        return None if v is None else week_ordered(v)

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

    @field_validator("race_date")
    @classmethod
    def race_date_is_iso_or_cleared(cls, value: Optional[str]) -> Optional[str]:
        """Absent or empty clears it; a value must be a real ISO date (#912).

        ``models.User.race_date`` is ``String(20)`` and the readers downstream
        call ``date.fromisoformat`` on it, so an over-long or nonsense value was
        either a 500 at write or a crash at read. The same ISO+calendar rule as
        the race event ``date`` closes both.
        """
        if value in (None, ""):
            return None
        return _require_iso_calendar_date(value)

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
            raise ValueError("resting_heart_rate must be below max_heart_rate")
        return self


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

    key_required: bool = False
    """Whether this athlete must supply a key before any AI call can succeed.

    The three booleans at the top describe the *account*; this one describes whether
    the account can actually be used, which also depends on the deployment —
    whether admin-key fallback is on, and whether the owner set a global key.
    Without it a client cannot tell "you have no key, and you need one" from
    "you have no key, and the server has one for you" (ai-trainer-ops#41).

    Computed by ``llm_service.user_must_supply_own_key``, which shares its rule
    with ``get_provider``, so this field and the 402 cannot disagree.

    Defaulted to ``False`` so an older client parsing a newer response keeps
    working, and so the field being absent never reads as "go and ask for a
    key".
    """
    allowance_tokens: Optional[int] = None
    """The free allowance on the owner's key, or ``None`` when unlimited (ops#79)."""
    allowance_used: int = 0
    allowance_window_days: int = 30


class AIKeySaveRequest(CamelModel):
    provider: str
    api_key: str = Field(min_length=1)


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
    sports: Optional[list[str]] = None
    training_days: Optional[list[str]] = None
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
    body_weight: Optional[str] = None
    """The athlete's weight, rendered as ``"62 kg"`` for the same reason
    (ai-trainer-ops#188). ``None`` when they have not entered one."""
    ftp_per_kg: Optional[str] = None
    """``current_ftp`` over the weight, as ``"4.5 W/kg"``: a climb target is
    W/kg, and the model's own division is the step most likely to go wrong."""
    fitness_level: str
    day_preferences: Optional[list[str]] = None
    """Weekdays preferred over others, as ``"Monday rather than Tuesday"``, the
    ones the plan can act on: both are training days (ai-trainer-ops#204)."""

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
            sports=user.sports,
            training_days=week_ordered(user.training_days)
            if user.training_days
            else user.training_days,
            follows_training_plan=user.follows_training_plan,
            max_heart_rate=user.max_heart_rate,
            resting_heart_rate=user.resting_heart_rate,
            current_ftp=user.current_ftp,
            threshold_pace=run_model.format_pace(
                run_model.speed_from_pace_seconds(user.threshold_pace_seconds_per_km)
            ),
            body_weight=f"{user.weight_kg:g} kg" if user.weight_kg else None,
            ftp_per_kg=(
                f"{user.current_ftp / user.weight_kg:.1f} W/kg"
                if user.current_ftp and user.weight_kg
                else None
            ),
            fitness_level=user.fitness_level or "",
            day_preferences=[
                day_preference.label(p)
                for p in day_preference.honoured(
                    user.day_preferences, user.training_days
                )
            ]
            or None,
        )
