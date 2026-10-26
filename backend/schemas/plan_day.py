"""A plan day: its sport, slot, intervals and strength, and how a day merges."""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from pydantic import (
    ConfigDict,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

from domain import untrusted_text
from domain.activity_identity import CYCLING_SPORT_TYPES, SPORT_CYCLING, training_sport
from domain.duration_range import describe_window, reconcile_duration
from domain.strength_model import MAX_RIR, normalize_exercise_name
from schemas.base import CamelModel, _to_camel
from schemas.workouts import WorkoutFeedbackSchema

logger = logging.getLogger(__name__)


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


class PowerRange(CamelModel):
    low: int
    high: int


class HeartRateRange(CamelModel):
    low: int
    high: int


class PlanInterval(CamelModel):
    duration: int  # seconds
    # Watts, for a ride at a known FTP. Optional since ai-trainer-ops#117: a run,
    # and an athlete with no power meter, have a heart rate or a pace instead, and
    # a required power pushed the model to invent watts or failed the plan.
    power: Optional[int] = None
    heart_rate: Optional[int] = None  # bpm
    pace: Optional[str] = None  # e.g. "4:30/km"
    rest: int  # seconds


# What a target can plausibly be (ai-trainer-ops#113). Outside it the value is
# a unit mistaken or a typo: "0.75" (a fraction of FTP) rendered as "0–0 W",
# "950–1200 bpm" was stored as written. Such a target is dropped, not guessed at:
# the session still says what to do in its words.
_TARGET_BOUNDS = {"target_power": (30, 2000), "target_heart_rate": (60, 220)}


def _bounded_target(value: Any, floor: int, ceiling: int) -> Any:
    """``value`` with its bounds in order, or None when it is no plausible target."""
    if not isinstance(value, dict):
        return value
    low, high = value.get("low"), value.get("high")
    if isinstance(low, bool) or isinstance(high, bool):
        return value
    try:
        low, high = sorted((int(low), int(high)))
    except (TypeError, ValueError, OverflowError):
        # Infinity or NaN from the model's JSON: no target to read. Dropped
        # like the other impossible ones, never a 500 (review of #926).
        return None
    if low < floor or high > ceiling:
        return None
    return {**value, "low": low, "high": high}


def _coerce_target_range(value: Any) -> Any:
    """Lenient coercion of a target power/HR value into a ``{low, high}`` range.

    The LLM occasionally emits a bare number (``"targetPower": 240``) or fills
    only one bound. Treat a scalar as a degenerate range and mirror a lone bound
    so one malformed value doesn't drop the whole day at the persist gate.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        v = int(value)
        return {"low": v, "high": v}
    if isinstance(value, str):
        try:
            v = int(float(value))
        except (ValueError, OverflowError):
            return None
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

    @model_validator(mode="before")
    @classmethod
    def _drop_stale_snake_twins_validator(cls, data: Any) -> Any:
        """Remove the snake_case twin of a modelled field the day also spells camel.

        ``extra="allow"`` is what keeps unmodelled keys from being silently
        dropped, and it has to stay. But a snake_case spelling of a *modelled*
        field is not unmodelled data — it is a second name for a field this model
        already owns, and when both names are present only one of them can win.
        Pydantic resolves that to the alias (camelCase), which this keeps; the
        other key then survives as an "extra" saying something the day no longer
        means. ``{"workoutType": "rest", "workout_type": "endurance"}`` validated
        and dumped straight back out as ``workoutType="rest"`` *and*
        ``workout_type="endurance"``, so which one a consumer believed depended on
        the order it read them in (ai-trainer-ops#29, #38).

        That is why ``_to_canonical_day`` was not canonical for the one input
        shape where canonicalisation matters most. Dropping the twin here fixes it
        for every writer at once rather than at each gate that remembers to.
        Camel precedence is deliberately *today's* resolution, so this removes a
        stale key and changes no value.

        Only exact snake_case twins of modelled fields are dropped. A key that is
        merely snake_case (``planner_note``) is unmodelled data and survives.

        The data markers go here too, for every writer at once: a plan writer
        shown titles marked «…» copied the markers into the days it wrote back
        (2026-10-15). They are never content; see ``untrusted_text.unmark``.
        """
        return untrusted_text.unmark(drop_stale_snake_twins(data))

    @field_validator("target_power", "target_heart_rate", mode="before")
    @classmethod
    def _coerce_ranges(cls, value: Any, info: ValidationInfo) -> Any:
        return _bounded_target(
            _coerce_target_range(value), *_TARGET_BOUNDS[info.field_name]
        )

    @field_validator(
        "duration_minutes",
        "duration_min_minutes",
        "duration_max_minutes",
        mode="before",
    )
    @classmethod
    def _whole_non_negative_minutes(cls, value: Any) -> Any:
        """90.7 minutes is 91, and no session lasts less than nothing.

        A float failed the int field, so a model writing "90.7" cost the whole
        generation three attempts and then a 502 (ai-trainer-ops#113).
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        # "Infinity" and "NaN" parse as floats, and round(inf) raises an
        # OverflowError pydantic does not turn into a ValidationError: a 500
        # (review of #926). Left to the int field, which rejects them.
        if isinstance(value, float) and not math.isfinite(value):
            return value
        return max(0, round(value))

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
        # coherent view. Shared arithmetic with normalize_duration_fields.
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
        # The description says the window the card shows, not a length of its
        # own outside it (ai-trainer-ops#180).
        self.description = describe_window(self.description, lo, hi)
        return self


# Back-compat alias: the canonical day model used to be TrainingDaySchema.
TrainingDaySchema = PlanDay


_DURATION_ALIAS_KEYS = frozenset(
    {"durationMinutes", "durationMinMinutes", "durationMaxMinutes"}
)


def drop_stale_snake_twins(data: Any) -> Any:
    """Strip the snake_case twin of any modelled field also spelled camelCase.

    Shared by :meth:`PlanDay._drop_stale_snake_twins_validator` and by
    :func:`canonical_plan_day`'s fallback, because the guarantee "the output
    holds at most one spelling of every modelled field" has to survive the one
    path where validation does *not* run. Found in review on PR #796: without
    this, a day malformed enough to fail ``PlanDay`` came back with both
    spellings intact, and the claim was true only of days that validate — which
    is precisely the kind of "holds unless someone forgot" rule
    ai-trainer-ops#38 exists to remove.

    Deciding which spelling stays does not depend on validation succeeding, so
    there is no reason for the two paths to disagree.
    """
    if not isinstance(data, dict):
        return data
    stale = [
        name
        for name, field in PlanDay.model_fields.items()
        if name in data
        and (alias := field.alias) is not None
        and alias != name
        and alias in data
    ]
    if not stale:
        return data
    return {k: v for k, v in data.items() if k not in stale}


def canonical_plan_day(day: dict) -> dict:
    """Validate + normalise one plan day through :class:`PlanDay`.

    The single canonicaliser. It guarantees a coherent duration (scalar vs
    min/max window), holds at most one spelling of every modelled field, and —
    because ``PlanDay`` uses ``extra="allow"`` — preserves any unmodelled key
    rather than dropping it. A day that fails validation (rare malformed
    legacy/LLM data) passes through unchanged and is logged, so one bad day
    never aborts a whole plan write (#422 follow-up).

    It lives here rather than in ``plan_pipeline`` so the constraint gate can
    reach it too: ``services.plan_constraints`` must not import the pipeline,
    and a second copy of this would be a second answer to "what is canonical"
    (ai-trainer-ops#38).
    """
    if not isinstance(day, dict):
        return day
    try:
        return PlanDay.model_validate(day).model_dump(
            by_alias=True, exclude_none=True, mode="json"
        )
    except Exception:  # noqa: BLE001 — never let one bad day block a write
        logger.warning(
            "plan day failed PlanDay validation; passing through unchanged",
            exc_info=True,
        )
        # Unchanged except for the stale twins, which are dropped here too so
        # the one-spelling guarantee does not quietly exclude the days that
        # need it most (review on PR #796).
        return drop_stale_snake_twins(day)


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
