"""What this athlete's freshness is *for*, and what a day should therefore be (#602).

The coach and the planner had stopped agreeing. The conversation layer has known
for a while what kind of rider it is talking to — the objective (#562), the
learned weights (#566), the rider identity (#597) — and the planner has known
none of it. It asked one question, *can the athlete handle another threshold
session?*, and it was the wrong question. The right one is:

    Is another threshold session the highest-value use of this athlete's
    freshness?

Those come apart exactly where the athlete lives. A trail rider who is recovered
enough for 3×12 at threshold in 34 °C heat, two days before the long off-road day
they actually train for, *can* do it. Spending the freshness there is still the
worse call, and no amount of physiology says so, because physiology is not what
is being traded.

So this module adds the missing term. Two ideas, both small:

* **Recovery value** — what freshness is worth to *this* athlete, per thing it
  can be spent on. Derived, never stored: it is a reading of the motivation model
  and the race calendar, so there is exactly one place an objective lives and
  this is not it. See :func:`recovery_value`.
* **The day board** — five day archetypes, scored on the axes the athlete's own
  weights already span, minus what the day costs in heat and in freshness that a
  higher-valued demand wanted. See :func:`allocate_day`.

Three properties, each of them a way this could have gone wrong:

* **A default athlete is unaffected.** With the cold-start weight vector and no
  learned affinity, the board still ranks the key session first. The whole point
  of #564's guard applies one level up: a planner that quietly reorders everyone's
  week is a worse product, not a better one.
* **Nothing new is persisted.** Recovery value is a function, not a table. The
  moment it becomes a column it is a third source of truth for what the athlete
  wants, drifting against the motivation model and the performance model.
* **Every deduction is named in the output.** A day that lost on heat and a day
  that lost on freshness lost for different reasons, and the coach has to be able
  to say which — the same reviewability rule :mod:`services.training_utility`
  keeps for its physiology/motivation split.

This module decides nothing about a specific date. It produces the priorities;
the planner places them. Fourteen days of weather and availability are the
planner's problem and it is already good at them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from services import motivation_model as mm
from services import training_utility as tu
from services.roi_recommendation import (
    GAIN_MAINTENANCE,
    GAIN_MODERATE,
    GAIN_RANK,
    SYSTEM_ENDURANCE,
    SYSTEM_THRESHOLD,
    SYSTEM_VO2MAX,
)
from services.weather_preference import (
    DIRECTION_SENSITIVE,
    DIRECTION_TOLERANT,
    HOT_C,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# The window a generated plan covers, so the heat question is asked of the days
# the plan is actually about.
PLAN_HORIZON_DAYS = 14

# --- What freshness gets spent on -------------------------------------------

DEMAND_TRAIL = "trail"
DEMAND_INTERVALS = "intervals"
DEMAND_RACE = "race"
DEMANDS: tuple[str, ...] = (DEMAND_TRAIL, DEMAND_INTERVALS, DEMAND_RACE)

_DEMAND_LABEL = {
    DEMAND_TRAIL: "the long off-road days they ride for",
    DEMAND_INTERVALS: "the next structured key session",
    DEMAND_RACE: "an event on the calendar",
}

# Recovery value is read off the weight vector, which sums to 1.0 across five
# axes — so a single axis is "large" at around 0.4, not at 0.8. Doubling puts a
# dominant weight near the top of the band scale without ever saturating it, and
# leaves the cold-start vector where it belongs: interval freshness matters most
# to an athlete nobody has learned anything about, because adaptation is what
# that vector is mostly made of.
VALUE_GAIN = 2.0

# Band boundaries, so the prompt can say "high" instead of "0.72" — a number the
# coach would otherwise be tempted to read back to the athlete as if it meant
# something to them.
BAND_HIGH = 0.6
BAND_MEDIUM = 0.3

# The modalities that count as off-road, i.e. the ones a trail demand is about.
_OFFROAD = (mm.MODALITY_MTB, mm.MODALITY_GRAVEL)


def band(value: float) -> str:
    """``high`` / ``medium`` / ``low`` for a 0–1 recovery value."""
    if value >= BAND_HIGH:
        return "high"
    if value >= BAND_MEDIUM:
        return "medium"
    return "low"


def recovery_value(
    motivation: Mapping[str, Any] | None,
    *,
    upcoming_races: int = 0,
) -> dict[str, float]:
    """What freshness is worth to this athlete, per thing it can be spent on.

    Each value is independent in [0, 1] — these are not shares of one budget.
    An athlete can care a great deal about arriving fresh to both the weekend
    trail day and the Tuesday intervals, and the answer to that is a week with
    fewer other hard days in it, not a forced ranking.

    Race freshness is zero with an empty calendar, the same rule
    :func:`services.training_utility.score_components` applies to race
    specificity: freshness for a race that does not exist is worth nothing, and
    keeping a race-shaped taper in the model of an athlete who has stopped racing
    is how a planner ends up defending decisions nobody asked for.
    """
    model = mm.normalize_model(motivation) if motivation else mm.default_model()
    weights = mm.normalize_weights(
        model.get("utility_weights"), pinned=model.get("pinned_weights")
    )
    affinity = mm.normalize_modality_affinity(model.get("modality_affinity"))

    offroad = max(affinity.get(m, mm.NEUTRAL_AFFINITY) for m in _OFFROAD)
    return {
        # Two things have to be true for trail freshness to matter: they enjoy
        # the riding, and the riding they enjoy is off-road. Either alone is not
        # it — an athlete with a high enjoyment weight who rides the road wants
        # their freshness somewhere else.
        DEMAND_TRAIL: _unit(VALUE_GAIN * offroad * weights["enjoyment"]),
        DEMAND_INTERVALS: _unit(VALUE_GAIN * weights["adaptation"]),
        DEMAND_RACE: (
            _unit(VALUE_GAIN * weights["race_performance"]) if upcoming_races else 0.0
        ),
    }


def _unit(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)


# --- The day board ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Archetype:
    """One kind of day, and what it costs to spend a day on it."""

    key: str
    label: str
    # The physiological system it delivers. Only used for the race-transfer
    # reading; the adaptation credit comes from ``gain_systems`` below.
    system: str
    # Which ROI systems supply this day's expected gain, so the existing chain
    # (#478) decides how much adaptation it is worth *for this athlete* rather
    # than a constant. Empty means maintenance by definition: a gym hour and a
    # rest day develop nothing, and crediting them with the moderate default
    # would let a rest day out-score a key session on physiology — which is
    # exactly the kind of scoring artefact that discredits a whole board.
    gain_systems: tuple[str, ...]
    # Candidate modalities, best affinity wins. A single-entry tuple pins it.
    modalities: tuple[str, ...]
    # Share of the athlete's freshness the session spends, 0–1.
    freshness_cost: float
    # The demands it serves, and therefore does not compete with.
    serves: tuple[str, ...]
    # Whether heat actually reaches it. A gym is a room.
    heat_exposed: bool
    # What the coach should call it when this day wins.
    prescription: str
    # How much it transfers to a result, overriding the per-system default where
    # the system alone does not distinguish the day. ``None`` keeps the default.
    race_specificity: float | None = None


# The board, in full. Five options because a day is a choice between kinds of
# day, not between sixteen (system, modality) pairs — that ranking already exists
# one level down in :mod:`services.training_utility` and answers a different
# question. The strength option is the one this whole issue is about: it is real
# training that costs almost no *riding* freshness, and it was not previously on
# any board the planner could see.
ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        key="key_session",
        label="structured key session (threshold/VO₂max intervals)",
        system=SYSTEM_THRESHOLD,
        gain_systems=(SYSTEM_THRESHOLD, SYSTEM_VO2MAX),
        modalities=(mm.MODALITY_INDOOR, mm.MODALITY_ROAD),
        freshness_cost=0.85,
        serves=(DEMAND_INTERVALS, DEMAND_RACE),
        heat_exposed=True,
        prescription="intervals",
    ),
    Archetype(
        key="endurance",
        label="steady aerobic endurance ride",
        system=SYSTEM_ENDURANCE,
        gain_systems=(SYSTEM_ENDURANCE,),
        # Deliberately not the technical off-road option: that is the next
        # archetype, and letting both resolve to the same bike would put the same
        # day on the board twice under two names.
        modalities=(mm.MODALITY_ROAD, mm.MODALITY_GRAVEL, mm.MODALITY_INDOOR),
        freshness_cost=0.4,
        serves=(),
        heat_exposed=True,
        prescription="endurance",
    ),
    Archetype(
        key="trail",
        label="off-road riding for terrain and handling, not for a power target",
        system=SYSTEM_ENDURANCE,
        gain_systems=(SYSTEM_ENDURANCE,),
        modalities=_OFFROAD,
        freshness_cost=0.5,
        serves=(DEMAND_TRAIL,),
        heat_exposed=True,
        prescription="endurance",
    ),
    Archetype(
        key="strength",
        label="upper-body and core strength with a mobility emphasis",
        system=tu.SYSTEM_STRENGTH,
        gain_systems=(),
        modalities=(mm.MODALITY_GYM,),
        # The number that makes this option exist. A gym hour is training, and it
        # takes almost nothing away from how the legs feel on Saturday.
        freshness_cost=0.1,
        serves=(),
        heat_exposed=False,
        prescription="strength",
    ),
    Archetype(
        key="recovery",
        label="easy spin or a full rest day",
        system=SYSTEM_ENDURANCE,
        gain_systems=(),
        modalities=(mm.MODALITY_INDOOR, mm.MODALITY_ROAD),
        freshness_cost=0.05,
        serves=(),
        heat_exposed=False,
        prescription="recovery",
        # An easy spin is the same *system* as the long ride and nothing like it
        # on race day. Without this override a rest day scores as race-specific
        # aerobic work for every athlete with an event on the calendar.
        race_specificity=0.05,
    ),
)

# How hard a competing demand pulls, relative to the 0–10 utility scale. Set so a
# full-cost day (0.85) against a high-value demand (~0.8) costs about 1.4 points
# — enough to lose a close call, never enough to bury a session the athlete's own
# weights genuinely want.
FRESHNESS_PENALTY_SCALE = 2.0

# What a hot day costs an exposed option, on the same 0–10 scale. The threshold
# is :data:`services.weather_preference.HOT_C`, deliberately the same boundary
# the plan prompts already flag, so an athlete is not told 29 °C is hot in one
# section and fine in the next.
HEAT_PENALTY = 2.0
# A learned tolerance is evidence and gets to act like it. Nagging a demonstrably
# heat-tolerant athlete off every warm day is its own failure mode, and one this
# codebase has already had to fix once.
HEAT_TOLERANCE_SCALE = {
    DIRECTION_TOLERANT: 0.35,
    DIRECTION_SENSITIVE: 1.35,
}


def _pick_modality(candidates: Sequence[str], affinity: Mapping[str, float]) -> str:
    """The candidate this athlete likes most; first listed on a tie."""
    return max(
        candidates,
        key=lambda m: (affinity.get(m, mm.NEUTRAL_AFFINITY), -candidates.index(m)),
    )


def _gain_for(archetype: Archetype, gain_map: Mapping[str, str] | None) -> str:
    """What this kind of day is worth to this athlete, per the ROI chain (#478).

    A day that could be delivered as either of two systems is worth the better of
    them — a "key session" is whichever of threshold and VO₂max this athlete's
    limiter actually rewards, and pinning it to one would understate the option
    for half the athletes.

    Falls back to ``moderate`` so an athlete with no confident limiter gets a
    board that is flat on physiology rather than one that invents a limiter —
    the same fallback :func:`services.roi_recommendation.recommend_training_roi`
    makes for itself.
    """
    if not archetype.gain_systems:
        return GAIN_MAINTENANCE
    gains = [
        (gain_map or {}).get(system, GAIN_MODERATE) for system in archetype.gain_systems
    ]
    return max(gains, key=lambda gain: GAIN_RANK.get(gain, 0))


def gain_map_from_recommendation(
    recommendation: Mapping[str, Any] | None,
) -> dict[str, str]:
    """The system → gain map out of an ROI recommendation, or empty."""
    if not recommendation or not recommendation.get("sufficient"):
        return {}
    return {
        str(entry.get("system")): str(entry.get("gain"))
        for entry in recommendation.get("expected_gain") or []
        if entry.get("system") and entry.get("gain")
    }


def allocate_day(
    motivation: Mapping[str, Any] | None,
    *,
    gain_map: Mapping[str, str] | None = None,
    upcoming_races: int = 0,
    hot: bool = False,
    heat_tolerance: str | None = None,
) -> dict[str, Any]:
    """Rank the kinds of day this athlete's freshness is best spent on.

    ``hot`` asks for the board as it stands on a day at or above
    :data:`services.weather_preference.HOT_C`; the caller decides whether the
    forecast contains such a day, because it has the forecast and this does not.

    The returned options each carry ``base`` (the athlete's own weighted score,
    unchanged from #564's axes), the two deductions by name, and ``score``. A day
    that lost to heat and a day that lost to a weekend the athlete cares about
    lost for different reasons and must be explainable as such.
    """
    model = mm.normalize_model(motivation) if motivation else mm.default_model()
    weights = mm.normalize_weights(
        model.get("utility_weights"), pinned=model.get("pinned_weights")
    )
    affinity = mm.normalize_modality_affinity(model.get("modality_affinity"))
    values = recovery_value(model, upcoming_races=upcoming_races)
    heat_scale = HEAT_TOLERANCE_SCALE.get(heat_tolerance or "", 1.0)

    options: list[dict[str, Any]] = []
    for archetype in ARCHETYPES:
        modality = _pick_modality(archetype.modalities, affinity)
        components = tu.score_components(
            archetype.system,
            modality,
            gain=_gain_for(archetype, gain_map),
            affinity=affinity,
            upcoming_races=upcoming_races,
            race_specificity=archetype.race_specificity,
        )
        base = sum(weights[c] * components[c] for c in mm.MOTIVATION_COMPONENTS)

        # Freshness is only *spent* against demands this day does not itself
        # serve. A threshold session does not compete with the interval
        # freshness it is the point of — it competes with the weekend.
        competing = [
            (demand, value)
            for demand, value in values.items()
            if demand not in archetype.serves
        ]
        contested, contested_value = max(
            competing, key=lambda item: item[1], default=("", 0.0)
        )
        freshness_cost = round(
            archetype.freshness_cost * contested_value * FRESHNESS_PENALTY_SCALE, 2
        )
        heat_cost = (
            round(HEAT_PENALTY * heat_scale, 2)
            if (hot and archetype.heat_exposed)
            else 0.0
        )

        options.append(
            {
                "key": archetype.key,
                "label": archetype.label,
                "prescription": archetype.prescription,
                "modality": modality,
                "base": round(base, 2),
                "heat_cost": heat_cost,
                "freshness_cost": freshness_cost,
                "competes_with": contested if freshness_cost else "",
                "score": round(base - heat_cost - freshness_cost, 2),
                "components": {k: round(v, 2) for k, v in components.items()},
            }
        )

    options.sort(key=lambda o: (o["score"], o["base"]), reverse=True)
    return {
        "options": options,
        "recovery_value": values,
        "weights": weights,
        "hot": hot,
        "heat_tolerance": heat_tolerance or "",
    }


def demand_label(demand: str) -> str:
    """The demand, in words the athlete would recognise."""
    return _DEMAND_LABEL.get(demand, demand)


# --- The planner turn -------------------------------------------------------


async def athlete_model_section_for_user(
    db: "AsyncSession",
    user: Any,
    *,
    timezone_name: str | None = None,
    horizon_days: int = PLAN_HORIZON_DAYS,
) -> str:
    """The whole athlete-model block for a plan prompt, or ``""``.

    Every plan entry point calls exactly this — the nightly regen and the manual
    "generate a plan" button must not disagree about who the athlete is, which
    is the same reason plan *writes* all go through one pipeline.

    Best-effort by construction. A plan is the product; a failure to read the
    athlete's objective degrades it to the physiology-first plan we shipped
    yesterday, and must never be the reason an athlete has no plan at all.
    """
    # Imported here rather than at module scope: these reach the DB and the
    # learning stack, and the scoring half of this module is pure and stays that
    # way so it can be unit-tested without a session.
    import crud
    import schemas
    from services import rider_identity, roi_recommendation
    from services.weather_preference import heat_tolerance_for_user
    from services.weather_service import daily_forecast_for_user

    # The same memory switch the durable profile is gated on everywhere else: an
    # athlete who turned memory off does not want their objective recalled into a
    # plan either.
    if not getattr(user, "memory_updates_enabled", False):
        return ""

    from services.prompts import planner_athlete_model_section

    user_id = user.id
    motivation: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    gain_map: dict[str, str] = {}
    upcoming_races = 0

    try:
        motivation_row = await crud.get_athlete_motivation_model(db, user_id)
        if motivation_row is not None:
            motivation = crud.motivation_model_as_dict(motivation_row)

        today = _today_iso(timezone_name)
        events = await crud.get_race_events(db, user_id)
        upcoming_races = sum(1 for event in events if (event.date or "") >= today)

        perf_row = await crud.get_athlete_performance_model(db, user_id)
        if perf_row is not None:
            performance_model = schemas.AthletePerformanceModelSchema.model_validate(
                perf_row, from_attributes=True
            ).model_dump(by_alias=False, mode="json")
            identity = await rider_identity.identity_for_prompt(
                db, user_id, performance_model=performance_model
            )
            gain_map = gain_map_from_recommendation(
                roi_recommendation.recommend_training_roi(
                    perf_row.attributes, perf_row.limiters
                )
            )
    except Exception:
        logger.warning("Planner athlete-model read failed", exc_info=True)
        return ""

    if motivation is None and identity is None:
        return ""

    # Heat only enters the board when the horizon actually contains a hot day.
    # Scoring every plan against 34 °C in February would be the same mistake as
    # ignoring it in August.
    hot = False
    heat_tolerance: str | None = None
    try:
        _, forecast = await daily_forecast_for_user(db, user_id, horizon_days)
        hot = any(
            isinstance(day.get("temperature_max_c"), (int, float))
            and float(day["temperature_max_c"]) >= HOT_C
            for day in forecast
        )
        if hot:
            heat_tolerance = await heat_tolerance_for_user(db, user_id)
    except Exception:
        logger.info("Planner heat lookup failed", exc_info=True)

    allocation = allocate_day(
        motivation,
        gain_map=gain_map,
        upcoming_races=upcoming_races,
        hot=hot,
        heat_tolerance=heat_tolerance,
    )
    return planner_athlete_model_section(
        motivation=motivation, identity=identity, allocation=allocation
    )


def _today_iso(timezone_name: str | None) -> str:
    from services.dates import app_today_iso

    return app_today_iso(timezone_name=timezone_name)
