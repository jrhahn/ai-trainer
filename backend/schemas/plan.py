"""Plan requests and responses: the plan, its rebuilds, the day board, the season."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from schemas.base import CamelModel, _to_camel


# ---------------------------------------------------------------------------
# Training plan
# ---------------------------------------------------------------------------


# What a client may store as its plan (ai-trainer-ops#283). The plan is kept as
# JSON and its titles and texts go into every coach prompt, so a request is
# held to sizes a real plan never reaches: a year of two sessions a day, the
# longest title and description a plan writer has produced many times over.
PLAN_REQUEST_MAX_DAYS = 800


PLAN_DAY_TITLE_MAX_CHARS = 200


PLAN_DAY_TEXT_MAX_CHARS = 4000


# The workout card a chat question is asked from rides into the prompt as JSON;
# a real one is a few kilobytes.
CONTEXT_WORKOUT_MAX_CHARS = 20000


class PlanRequest(BaseModel):
    """Frontend sends the plan as an array of raw JSON dicts (camelCase from AI)."""

    plan: list[Any] = Field(max_length=PLAN_REQUEST_MAX_DAYS)

    @field_validator("plan")
    @classmethod
    def _days_within_bounds(cls, days: list[Any]) -> list[Any]:
        for day in days:
            if not isinstance(day, dict):
                raise ValueError("every plan day must be an object")
            if len(str(day.get("title") or "")) > PLAN_DAY_TITLE_MAX_CHARS:
                raise ValueError(
                    f"a plan day's title is limited to {PLAN_DAY_TITLE_MAX_CHARS} characters"
                )
            for field in ("description", "workoutPurpose"):
                if len(str(day.get(field) or "")) > PLAN_DAY_TEXT_MAX_CHARS:
                    raise ValueError(
                        f"a plan day's {field} is limited to {PLAN_DAY_TEXT_MAX_CHARS} characters"
                    )
        return days


class PlanResponse(BaseModel):
    plan: list[Any]
    # The plan's texts already translated into the interface language, keyed
    # by the English text (ai-trainer-ops#121, #271). Only GET /users/me/plan
    # fills it; empty for an interface that is not translated.
    translations: dict[str, str] = Field(default_factory=dict)


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


class SeasonWeekOut(CamelModel):
    """One week of the season plan (ai-trainer-ops#98). Dates are ISO strings."""

    week_start: str
    phase: str
    focus: str
    recovery: bool
    load_week: Optional[int] = None
    hours_min: Optional[float] = None
    hours_max: Optional[float] = None
    hard_sessions_min: int
    hard_sessions_max: int
    key_session: str
    next_race: Optional[str] = None
    # None while the week has no goal written for it as it is now.
    goal: Optional[str] = None


class SeasonPlanResponse(CamelModel):
    weeks: list[SeasonWeekOut]


class PlanTranslationsResponse(CamelModel):
    """The plan's texts in the interface language (ai-trainer-ops#121).

    ``translations`` maps an English plan text to its translation. ``language``
    is the code they are in, or None when the interface language is not one
    the plan is translated into.
    """

    language: Optional[str] = None
    translations: dict[str, str] = {}
    # Whether texts are still untranslated: the page then asks the POST to
    # write them, and asks again while a write leaves some.
    missing: bool = False
    # Which texts are still untranslated, as a short key: the page asks for a
    # write once per key (ai-trainer-ops#215). None when nothing is missing.
    missing_key: Optional[str] = None


class GeneratePlanRequest(CamelModel):
    pass


class PlanRebuildSchema(CamelModel):
    """A rebuild the app made by itself (ai-trainer-ops#206)."""

    at: str
    reasons: list[str] = Field(default_factory=list)


class PlanInputsChangedResponse(CamelModel):
    """What moved since the plan was generated (ai-trainer-ops#158)."""

    changed: bool = False
    # Codes from ``services.plan_inputs.REASONS``; the app words them.
    reasons: list[str] = Field(default_factory=list)
    # Full rebuilds still allowed on the athlete's day (ai-trainer-ops#175).
    rebuilds_left: int = 0
    # The last rebuild the app made by itself, and why (ai-trainer-ops#206).
    last_rebuild: Optional[PlanRebuildSchema] = None


class PlanRebuildResponse(CamelModel):
    """``POST /ai/plan-rebuild``: whether the athlete's own change rebuilt the plan."""

    rebuilt: bool = False
    reasons: list[str] = Field(default_factory=list)
    # The new plan when rebuilt.
    plan: Optional[list[Any]] = None
    # True when a change waits because today's rebuilds are used up.
    limited: bool = False


class DayBoardOptionSchema(CamelModel):
    """One kind of day on the board, with each deduction by name (ai-trainer-ops#17)."""

    key: str
    label: str
    prescription: str
    modality: str
    base: float
    heat_cost: float
    freshness_cost: float
    competes_with: str = ""
    competes_with_label: str = ""
    score: float


class CodeSourceSchema(CamelModel):
    """Where in the public repository the numbers were computed."""

    path: str
    line: int
    symbol: str
    # The commit the line number belongs to: the deployed one, or ``develop``
    # when the backend does not know (local runs).
    ref: str = "develop"


class DayBoardResponse(CamelModel):
    """The day board the planner got, for the athlete to read (ai-trainer-ops#17, #39 Q9).

    ``options`` is empty when nothing is known about the athlete yet, or when
    memory is off; the planner then gets no board either.
    """

    hot: bool = False
    heat_tolerance: str = ""
    options: list[DayBoardOptionSchema] = []
    source: CodeSourceSchema
