"""Logged workouts, manual activities and strength sets."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, field_validator
from schemas.base import CamelModel


# ---------------------------------------------------------------------------
# Workout logs
# ---------------------------------------------------------------------------


class WorkoutFeedbackSchema(CamelModel):
    actual_duration_minutes: int
    average_power: Optional[int] = None
    average_heart_rate: Optional[int] = None
    peak_power: Optional[int] = None
    # The head unit's normalized power and the distance, when the athlete said
    # (ai-trainer-ops#169, #171). ``None`` is "not said", never 0.
    normalized_power: Optional[int] = None
    distance_km: Optional[float] = None
    perceived_effort: int
    notes: str = ""
    completed_at: str


DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class WorkoutFeedbackRequest(WorkoutFeedbackSchema):
    """The log of a planned session as a client submits it (ai-trainer-ops#127).

    Bounded like a hand-entered activity: 100,000,000 minutes became a CTL of
    1.8 million and a coach saying rest, and -5 min, an effort of 999 and 1 MB
    of notes were stored too. Only the request is bounded: the same schema
    reads the feedback stored on plan days, where an old out-of-range value
    must not stop a plan from being saved.
    """

    actual_duration_minutes: int = Field(ge=1, le=24 * 60)
    average_power: Optional[int] = Field(default=None, ge=0, le=2500)
    average_heart_rate: Optional[int] = Field(default=None, ge=30, le=250)
    peak_power: Optional[int] = Field(default=None, ge=0, le=3000)
    normalized_power: Optional[int] = Field(default=None, ge=1, le=2500)
    distance_km: Optional[float] = Field(default=None, gt=0, le=1000)
    # This app's 1-5 scale, as the form offers and the load reads (#712).
    perceived_effort: int = Field(ge=1, le=5)
    notes: str = Field(default="", max_length=2000)
    # An ISO timestamp, as the form sends it; the column holds 50 characters.
    completed_at: str = Field(
        max_length=50,
        pattern=r"^\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$",
    )

    @field_validator("completed_at")
    @classmethod
    def _a_real_timestamp(cls, value: str) -> str:
        """The shape is not enough: "2026-05-01T99:99" and "…T::.." passed it
        (review of #940). It has to parse, as every reader parses it."""
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class ManualActivityRequest(CamelModel):
    """A session the athlete did and enters by hand (ai-trainer-ops#47).

    Not tied to a plan day: a ride on a rest day, a run when a ride was planned,
    a second session, a day the plan never covered. The same fields as the log
    of a planned session minus everything that needs a device (power), because
    the athlete without a recording is exactly who this is for.
    """

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    sport: str
    duration_minutes: int = Field(ge=1, le=24 * 60)
    # The same 1–5 scale the log form uses and the load ladder reads (#712).
    perceived_effort: int = Field(ge=1, le=5)
    average_heart_rate: Optional[int] = Field(default=None, ge=30, le=250)
    # A ride's normalized power from the head unit (ai-trainer-ops#169).
    normalized_power: Optional[int] = Field(default=None, ge=1, le=2500)
    # Optional, and only meaningful for the sports that cover ground. Kilometres
    # because that is what the athlete types; stored as given, never derived from
    # a pace. 1000 km is a bound on typos, not on ambition.
    distance_km: Optional[float] = Field(default=None, gt=0, le=1000)
    notes: str = Field(default="", max_length=2000)


class ManualActivityUpdate(CamelModel):
    """A correction to an entered activity (#47). The day is its identity, so
    moving one to another day is a delete and a new entry."""

    sport: str
    duration_minutes: int = Field(ge=1, le=24 * 60)
    perceived_effort: int = Field(ge=1, le=5)
    average_heart_rate: Optional[int] = Field(default=None, ge=30, le=250)
    # A ride's normalized power from the head unit (ai-trainer-ops#169).
    normalized_power: Optional[int] = Field(default=None, ge=1, le=2500)
    # Optional, and only meaningful for the sports that cover ground. Kilometres
    # because that is what the athlete types; stored as given, never derived from
    # a pace. 1000 km is a bound on typos, not on ambition.
    distance_km: Optional[float] = Field(default=None, gt=0, le=1000)
    notes: str = Field(default="", max_length=2000)


class ManualActivityResponse(CamelModel):
    date: str
    slot: int
    sport: str


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

    The derived fields are computed on read by ``domain.strength_model`` rather
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

    feedback: WorkoutFeedbackRequest
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
