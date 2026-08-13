"""Per-athlete weather tolerances as confidence-scored beliefs (#495).

"Everyone slows in the heat" is a population average, not an athlete. Some riders
hold power at 34 °C; some love a wet day and will go out regardless; some quietly
move every cold session onto the trainer. This engine learns which of those is
true for *this* athlete and, crucially, how sure we are — so recommendations can
reveal uncertainty rather than assert one truth (#490) and the coach stops nagging
a demonstrably heat-tolerant rider off every warm day.

The learning substrate already exists: every imported ride persists the conditions
it was ridden in on :class:`models.RideMetric` (``weather_temperature_c``,
``weather_apparent_temperature_c``, ``weather_condition``,
``weather_precipitation_mm``, ``weather_wind_speed_kph``). What was missing is the
correlation and belief update, which is what this module adds. Two evidence
streams feed it:

* **Outcome** — did intensity and duration hold up in a condition bucket
  (hot / cold / wet / windy) compared with the athlete's own mild-weather
  baseline? Intensity is read as the stored ``intensity_factor`` (NP/FTP), the
  only power measure comparable across a season of changing FTP, and never
  recomputed from downsampled streams (#466).
* **Behaviour** — did they ride outdoors anyway when conditions turned, or move
  indoors? Indoor rides carry no GPS, so their conditions come from the athlete's
  persisted training location (``weather_source="open_meteo_home"``, see
  :func:`services.weather_service.enrich_activity_weather`).

Beliefs are stored as :class:`models.AthleteHypothesis` rows under
:data:`CATEGORY`, sharing the existing merge/decay lifecycle: a recurring belief
accrues evidence and confidence, one the data no longer supports decays and is
retired. An athlete's own stated preference ("I actually love the rain") is
captured from chat as first-class evidence and is exempt from that decay — absent
ride data is not a counter-argument to what they told us.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models

logger = logging.getLogger(__name__)

# All weather-preference beliefs share one category so they reconcile together
# without touching the performance-model or LLM-formed hypotheses.
CATEGORY = "weather_preference"

# Condition thresholds. Deliberately the same boundaries the coaching
# ``weather_load_flag`` uses, so what the athlete is judged on matches what the
# plan prompts flag.
HOT_C = 29.0
COLD_C = 8.0
WET_PRECIPITATION_MM = 0.5
WET_CONDITIONS = frozenset({"rain", "drizzle", "snow", "thunderstorm"})
WINDY_KPH = 25.0

# Evidence gates. Below these there is not enough history to assert even a
# tentative claim, so nothing is written at all.
MIN_BUCKET_RIDES = 3
MIN_BASELINE_RIDES = 5
MIN_BEHAVIOUR_RIDES = 5

# Effect sizes that separate "held up", "dropped" and "inconclusive". A ride in
# adverse weather is expected to be slightly easier, so the tolerant gate sits
# just below parity rather than at it.
TOLERANT_INTENSITY_RATIO = 0.98
TOLERANT_DURATION_RATIO = 0.95
SENSITIVE_INTENSITY_RATIO = 0.93
SENSITIVE_DURATION_RATIO = 0.85

# Outdoor share of adverse-weather rides that reads as "goes out regardless" vs
# "retreats indoors".
OUTDOOR_COMMITTED_SHARE = 0.8
INDOOR_LEANING_SHARE = 0.4

DIMENSION_HEAT = "heat"
DIMENSION_COLD = "cold"
DIMENSION_RAIN = "rain"
DIMENSION_WIND = "wind"

DIRECTION_TOLERANT = "tolerant"
DIRECTION_SENSITIVE = "sensitive"

# Marker that prefixes evidence the athlete stated themselves. Used to exempt
# those beliefs from the "no supporting ride data" decay pass.
ATHLETE_STATED_PREFIX = "Athlete said:"

# Canonical statement text per (dimension, direction). The statement *is* the
# merge key (``crud.propose_athlete_hypothesis`` normalises it), so it must stay
# stable and free of varying numbers — both ride evidence and chat evidence
# reinforce the same row through here.
_STATEMENTS: dict[tuple[str, str], str] = {
    (DIMENSION_HEAT, DIRECTION_TOLERANT): (
        "This athlete tolerates heat well — intensity and duration hold up on hot "
        "days rather than falling away."
    ),
    (DIMENSION_HEAT, DIRECTION_SENSITIVE): (
        "This athlete is heat-sensitive — intensity or duration drops on hot days "
        "compared with their mild-weather sessions."
    ),
    (DIMENSION_COLD, DIRECTION_TOLERANT): (
        "This athlete handles cold well — sessions in the cold hold their "
        "intensity and length."
    ),
    (DIMENSION_COLD, DIRECTION_SENSITIVE): (
        "This athlete is cold-sensitive — sessions in the cold come out shorter or "
        "easier than their mild-weather baseline."
    ),
    (DIMENSION_RAIN, DIRECTION_TOLERANT): (
        "This athlete does not mind riding in the rain — wet sessions get done "
        "outdoors and hold up."
    ),
    (DIMENSION_RAIN, DIRECTION_SENSITIVE): (
        "This athlete avoids or struggles in the rain — wet sessions get cut "
        "short, softened, or moved indoors."
    ),
    (DIMENSION_WIND, DIRECTION_TOLERANT): (
        "This athlete rides well in strong wind — windy sessions hold their "
        "intensity and length."
    ),
    (DIMENSION_WIND, DIRECTION_SENSITIVE): (
        "This athlete is wind-sensitive — windy sessions come out easier or "
        "shorter than their mild-weather baseline."
    ),
}

_ALTERNATIVES: dict[str, list[str]] = {
    DIMENSION_HEAT: [
        "The hot rides may simply have been easier sessions by design "
        "(recovery/endurance) rather than evidence of heat tolerance.",
        "Hot days may coincide with a season block that is easier or harder overall.",
    ],
    DIMENSION_COLD: [
        "Cold rides cluster in the off-season, when sessions are easier for "
        "periodisation reasons rather than because of the cold.",
        "Cold-weather kit and route choice, not physiology, may explain the "
        "difference.",
    ],
    DIMENSION_RAIN: [
        "Wet rides may be short commutes rather than deliberate training.",
        "The athlete may ride in rain out of schedule pressure rather than "
        "preference.",
    ],
    DIMENSION_WIND: [
        "Wind speed at the training base may not reflect the sheltered routes "
        "actually ridden.",
        "Windy days may coincide with group rides that change the intensity "
        "regardless of wind.",
    ],
}


def preference_statement(dimension: str, direction: str) -> str | None:
    """Return the canonical belief text for a ``(dimension, direction)`` pair."""
    return _STATEMENTS.get((dimension, direction))


@dataclass(slots=True)
class RideWeather:
    """One ride reduced to the fields the weather-preference update needs."""

    date: str
    outdoor: bool
    temperature_c: float | None
    condition: str | None
    precipitation_mm: float | None
    wind_speed_kph: float | None
    duration_seconds: int | None
    intensity_factor: float | None

    @property
    def is_hot(self) -> bool:
        return self.temperature_c is not None and self.temperature_c >= HOT_C

    @property
    def is_cold(self) -> bool:
        return self.temperature_c is not None and self.temperature_c < COLD_C

    @property
    def is_wet(self) -> bool:
        if self.precipitation_mm is not None and self.precipitation_mm >= WET_PRECIPITATION_MM:
            return True
        return self.condition in WET_CONDITIONS

    @property
    def is_windy(self) -> bool:
        return (
            self.wind_speed_kph is not None and self.wind_speed_kph >= WINDY_KPH
        )

    @property
    def is_mild(self) -> bool:
        """Baseline conditions: nothing about the weather stands out."""
        return (
            self.temperature_c is not None
            and COLD_C <= self.temperature_c < HOT_C
            and not self.is_wet
            and not self.is_windy
        )

    @property
    def is_adverse(self) -> bool:
        return self.is_hot or self.is_cold or self.is_wet or self.is_windy


def ride_weather_from_metric(metric: models.RideMetric) -> RideWeather:
    """Project a stored :class:`models.RideMetric` onto :class:`RideWeather`.

    Uses the apparent ("feels like") temperature when available — that is what the
    athlete actually experienced, and it is what humidity-driven heat stress shows
    up in. A ride counts as outdoor when it has measured GPS; an indoor/trainer
    ride has none and only carries home-location weather.
    """
    return RideWeather(
        date=metric.activity_date,
        outdoor=metric.start_lat is not None and metric.start_lng is not None,
        temperature_c=(
            metric.weather_apparent_temperature_c
            if metric.weather_apparent_temperature_c is not None
            else metric.weather_temperature_c
        ),
        condition=metric.weather_condition,
        precipitation_mm=metric.weather_precipitation_mm,
        wind_speed_kph=metric.weather_wind_speed_kph,
        duration_seconds=metric.duration_seconds,
        intensity_factor=metric.intensity_factor,
    )


def _mean_intensity(rides: Iterable[RideWeather]) -> float | None:
    values = [
        r.intensity_factor
        for r in rides
        if r.intensity_factor is not None and r.intensity_factor > 0
    ]
    return fmean(values) if values else None


def _mean_duration_minutes(rides: Iterable[RideWeather]) -> float | None:
    values = [
        r.duration_seconds / 60.0
        for r in rides
        if r.duration_seconds is not None and r.duration_seconds > 0
    ]
    return fmean(values) if values else None


def _confidence(sample: int, effect: float) -> float:
    """Confidence from how much evidence there is and how big the effect is.

    Never certain and never a guess: it starts at the "tentative claim" floor,
    grows with the number of rides past the evidence gate, and grows a little more
    when the measured difference is large. Capped well below 1.0 — a correlation
    over a season of rides is not proof.
    """
    sample_term = min(0.30, 0.05 * max(0, sample - MIN_BUCKET_RIDES + 1))
    effect_term = min(0.15, abs(effect) * 1.5)
    return round(min(0.80, 0.30 + sample_term + effect_term), 2)


def _belief(
    dimension: str,
    direction: str,
    confidence: float,
    evidence: list[str],
) -> dict[str, Any] | None:
    statement = preference_statement(dimension, direction)
    if statement is None or not evidence:
        return None
    return {
        "dimension": dimension,
        "direction": direction,
        "statement": statement,
        "category": CATEGORY,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "rationale": " ".join(evidence),
        "evidence": evidence,
        "alternative_explanations": list(_ALTERNATIVES.get(dimension, [])),
    }


_BUCKET_LABELS = {
    DIMENSION_HEAT: f"hot (>={HOT_C:g} C felt)",
    DIMENSION_COLD: f"cold (<{COLD_C:g} C felt)",
    DIMENSION_RAIN: "wet",
    DIMENSION_WIND: f"windy (>={WINDY_KPH:g} kph)",
}


def _outcome_belief(
    dimension: str,
    bucket: list[RideWeather],
    baseline: list[RideWeather],
) -> dict[str, Any] | None:
    """Compare one condition bucket against the athlete's mild-weather baseline."""
    if len(bucket) < MIN_BUCKET_RIDES or len(baseline) < MIN_BASELINE_RIDES:
        return None

    bucket_intensity = _mean_intensity(bucket)
    baseline_intensity = _mean_intensity(baseline)
    bucket_duration = _mean_duration_minutes(bucket)
    baseline_duration = _mean_duration_minutes(baseline)

    intensity_ratio = (
        bucket_intensity / baseline_intensity
        if bucket_intensity and baseline_intensity
        else None
    )
    duration_ratio = (
        bucket_duration / baseline_duration
        if bucket_duration and baseline_duration
        else None
    )
    if intensity_ratio is None and duration_ratio is None:
        return None

    label = _BUCKET_LABELS[dimension]
    evidence: list[str] = []
    if intensity_ratio is not None:
        evidence.append(
            f"Across {len(bucket)} {label} rides, intensity factor averaged "
            f"{bucket_intensity:.2f} against {baseline_intensity:.2f} over "
            f"{len(baseline)} mild-weather rides ({intensity_ratio:.0%} of baseline)."
        )
    if duration_ratio is not None:
        evidence.append(
            f"Session length averaged {round(bucket_duration)} min against "
            f"{round(baseline_duration)} min in mild weather "
            f"({duration_ratio:.0%} of baseline)."
        )

    held_intensity = intensity_ratio is None or intensity_ratio >= TOLERANT_INTENSITY_RATIO
    held_duration = duration_ratio is None or duration_ratio >= TOLERANT_DURATION_RATIO
    dropped_intensity = (
        intensity_ratio is not None and intensity_ratio <= SENSITIVE_INTENSITY_RATIO
    )
    dropped_duration = (
        duration_ratio is not None and duration_ratio <= SENSITIVE_DURATION_RATIO
    )

    effect = max(
        abs(1.0 - intensity_ratio) if intensity_ratio is not None else 0.0,
        abs(1.0 - duration_ratio) if duration_ratio is not None else 0.0,
    )
    if held_intensity and held_duration:
        direction = DIRECTION_TOLERANT
    elif dropped_intensity or dropped_duration:
        direction = DIRECTION_SENSITIVE
    else:
        # Between the gates: a real but unremarkable difference. Asserting either
        # claim here would be inventing a pattern.
        return None

    return _belief(dimension, direction, _confidence(len(bucket), effect), evidence)


def _behaviour_belief(rides: list[RideWeather]) -> dict[str, Any] | None:
    """Judge rain tolerance from what the athlete actually did in wet weather.

    Behaviour is the strongest available signal for rain: an athlete who keeps
    heading out in the wet has told us something no power number does, and one who
    consistently retreats to the trainer has too.
    """
    wet = [r for r in rides if r.is_wet]
    if len(wet) < MIN_BEHAVIOUR_RIDES:
        return None
    outdoor = [r for r in wet if r.outdoor]
    share = len(outdoor) / len(wet)

    if share >= OUTDOOR_COMMITTED_SHARE:
        return _belief(
            DIMENSION_RAIN,
            DIRECTION_TOLERANT,
            _confidence(len(wet), share - 0.5),
            [
                f"{len(outdoor)} of {len(wet)} rides in wet conditions were ridden "
                f"outdoors ({share:.0%}) — the athlete goes out anyway."
            ],
        )
    if share <= INDOOR_LEANING_SHARE:
        return _belief(
            DIMENSION_RAIN,
            DIRECTION_SENSITIVE,
            _confidence(len(wet), 0.5 - share),
            [
                f"Only {len(outdoor)} of {len(wet)} rides in wet conditions happened "
                f"outdoors ({share:.0%}) — the rest moved indoors."
            ],
        )
    return None


def derive_weather_preferences(rides: list[RideWeather]) -> list[dict[str, Any]]:
    """Map a ride history to weather-preference beliefs, strongest first.

    Pure and side-effect free so the correlation logic is testable without a
    database. Returns an empty list when the history has no bucket confident
    enough to support a claim — silence is the honest output for a thin history.
    """
    if not rides:
        return []

    outdoor = [r for r in rides if r.outdoor]
    baseline = [r for r in outdoor if r.is_mild]

    beliefs: list[dict[str, Any]] = []
    for dimension, predicate in (
        (DIMENSION_HEAT, lambda r: r.is_hot),
        (DIMENSION_COLD, lambda r: r.is_cold),
        (DIMENSION_RAIN, lambda r: r.is_wet),
        (DIMENSION_WIND, lambda r: r.is_windy),
    ):
        belief = _outcome_belief(
            dimension, [r for r in outdoor if predicate(r)], baseline
        )
        if belief is not None:
            beliefs.append(belief)

    behaviour = _behaviour_belief(rides)
    if behaviour is not None:
        # Behaviour and outcome can both speak to rain. Merge them into the
        # stronger-confidence single belief rather than asserting two rows, and
        # drop a behaviour reading that contradicts the outcome reading — a
        # contradiction means we do not know, so we should not claim.
        existing = next(
            (b for b in beliefs if b["dimension"] == DIMENSION_RAIN), None
        )
        if existing is None:
            beliefs.append(behaviour)
        elif existing["direction"] == behaviour["direction"]:
            existing["evidence"] = [*existing["evidence"], *behaviour["evidence"]]
            existing["rationale"] = " ".join(existing["evidence"])
            existing["confidence"] = round(
                min(0.85, max(existing["confidence"], behaviour["confidence"]) + 0.05),
                2,
            )
        else:
            beliefs = [b for b in beliefs if b["dimension"] != DIMENSION_RAIN]

    beliefs.sort(key=lambda b: b["confidence"], reverse=True)
    return beliefs


# ---------------------------------------------------------------------------
# Athlete-stated preferences from chat
# ---------------------------------------------------------------------------

# High-confidence phrasing only: an athlete complaining that "today was hot" is
# not stating a tolerance. Each entry maps a pattern to (dimension, direction).
_PREFERENCE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"\b(?:i\s+)?(?:love|enjoy|like)\s+(?:riding\s+|training\s+)?(?:in\s+)?the\s+rain\b", DIMENSION_RAIN, DIRECTION_TOLERANT),
    (r"\b(?:i\s+)?(?:don't|do\s+not|dont)\s+mind\s+(?:the\s+)?rain\b", DIMENSION_RAIN, DIRECTION_TOLERANT),
    (r"\brain\s+(?:doesn't|does\s+not|doesnt)\s+bother\s+me\b", DIMENSION_RAIN, DIRECTION_TOLERANT),
    (r"\bich\s+(?:liebe|mag)\s+(?:den\s+)?regen\b", DIMENSION_RAIN, DIRECTION_TOLERANT),
    (r"\bregen\s+macht\s+mir\s+nichts\b", DIMENSION_RAIN, DIRECTION_TOLERANT),
    (r"\b(?:i\s+)?(?:hate|can't\s+stand|cannot\s+stand)\s+(?:riding\s+)?(?:in\s+)?the\s+rain\b", DIMENSION_RAIN, DIRECTION_SENSITIVE),
    (r"\b(?:i\s+)?(?:won't|will\s+not|refuse\s+to)\s+ride\s+in\s+the\s+rain\b", DIMENSION_RAIN, DIRECTION_SENSITIVE),
    (r"\bich\s+hasse\s+(?:den\s+)?regen\b", DIMENSION_RAIN, DIRECTION_SENSITIVE),
    (r"\b(?:i\s+)?(?:love|enjoy|thrive\s+in)\s+the\s+heat\b", DIMENSION_HEAT, DIRECTION_TOLERANT),
    (r"\b(?:the\s+)?heat\s+(?:doesn't|does\s+not|doesnt)\s+bother\s+me\b", DIMENSION_HEAT, DIRECTION_TOLERANT),
    (r"\bhitze\s+macht\s+mir\s+nichts\b", DIMENSION_HEAT, DIRECTION_TOLERANT),
    (r"\b(?:i\s+)?(?:hate|can't\s+stand|cannot\s+stand)\s+the\s+heat\b", DIMENSION_HEAT, DIRECTION_SENSITIVE),
    (r"\b(?:the\s+)?heat\s+(?:kills|destroys|wrecks)\s+me\b", DIMENSION_HEAT, DIRECTION_SENSITIVE),
    (r"\bich\s+(?:hasse|vertrage\s+keine)\s+hitze\b", DIMENSION_HEAT, DIRECTION_SENSITIVE),
    (r"\b(?:i\s+)?(?:love|enjoy|like)\s+(?:riding\s+)?(?:in\s+)?the\s+cold\b", DIMENSION_COLD, DIRECTION_TOLERANT),
    (r"\b(?:the\s+)?cold\s+(?:doesn't|does\s+not|doesnt)\s+bother\s+me\b", DIMENSION_COLD, DIRECTION_TOLERANT),
    (r"\bk(?:ä|ae)lte\s+macht\s+mir\s+nichts\b", DIMENSION_COLD, DIRECTION_TOLERANT),
    (r"\b(?:i\s+)?(?:hate|can't\s+stand|cannot\s+stand)\s+(?:riding\s+)?(?:in\s+)?the\s+cold\b", DIMENSION_COLD, DIRECTION_SENSITIVE),
    (r"\bich\s+hasse\s+(?:die\s+)?k(?:ä|ae)lte\b", DIMENSION_COLD, DIRECTION_SENSITIVE),
    (r"\b(?:i\s+)?(?:don't|do\s+not|dont)\s+mind\s+(?:the\s+)?wind\b", DIMENSION_WIND, DIRECTION_TOLERANT),
    (r"\b(?:i\s+)?(?:hate|can't\s+stand|cannot\s+stand)\s+(?:riding\s+in\s+)?(?:the\s+)?wind\b", DIMENSION_WIND, DIRECTION_SENSITIVE),
    (r"\bich\s+hasse\s+(?:den\s+)?wind\b", DIMENSION_WIND, DIRECTION_SENSITIVE),
)

# The athlete telling us directly is strong evidence — stronger than a
# correlation over a handful of rides — but still not certainty.
ATHLETE_STATED_CONFIDENCE = 0.7


def extract_weather_preference_statements(text: str) -> list[dict[str, str]]:
    """Extract weather preferences the athlete stated in their own words.

    Returns ``[{"dimension", "direction", "statement"}]``, de-duplicated per
    dimension (the first match for a dimension wins, so a mixed message does not
    assert both directions at once).
    """
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return []

    found: dict[str, dict[str, str]] = {}
    for pattern, dimension, direction in _PREFERENCE_PATTERNS:
        if dimension in found:
            continue
        if re.search(pattern, normalized):
            statement = preference_statement(dimension, direction)
            if statement:
                found[dimension] = {
                    "dimension": dimension,
                    "direction": direction,
                    "statement": statement,
                }
    return list(found.values())


async def capture_weather_preferences_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
    *,
    now: datetime | None = None,
) -> list[models.AthleteHypothesis]:
    """Record weather preferences stated in a coach-chat message as evidence.

    Reinforces the same belief rows the ride data writes (statements are shared
    through :func:`preference_statement`), so a stated preference and a measured
    one compound instead of competing. Best-effort: a failure here must never
    break the chat turn.
    """
    statements = extract_weather_preference_statements(message)
    if not statements:
        return []

    quote = " ".join((message or "").split())[:200]
    rows: list[models.AthleteHypothesis] = []
    for item in statements:
        try:
            row = await crud.propose_athlete_hypothesis(
                db,
                user_id,
                statement=item["statement"],
                category=CATEGORY,
                rationale=f'{ATHLETE_STATED_PREFIX} "{quote}"',
                confidence=ATHLETE_STATED_CONFIDENCE,
                evidence=[f'{ATHLETE_STATED_PREFIX} "{quote}"'],
                alternative_explanations=list(
                    _ALTERNATIVES.get(item["dimension"], [])
                ),
                observed_at=now,
            )
            rows.append(row)
        except Exception:
            logger.warning(
                "Could not record stated weather preference for user_id=%s",
                user_id,
                exc_info=True,
            )
    return rows


# ---------------------------------------------------------------------------
# Persistence + prompt context
# ---------------------------------------------------------------------------


def _is_athlete_stated(row: models.AthleteHypothesis) -> bool:
    if isinstance(row.evidence, list):
        if any(
            isinstance(item, str) and item.startswith(ATHLETE_STATED_PREFIX)
            for item in row.evidence
        ):
            return True
    return (row.rationale or "").startswith(ATHLETE_STATED_PREFIX)


async def refresh_weather_preferences(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
) -> int:
    """Regenerate and persist the athlete's weather-preference beliefs.

    Mirrors the deterministic performance-hypothesis engine (#479): derive from
    stored data, reinforce each belief through the merge lifecycle, then decay any
    belief the data no longer supports. Beliefs the athlete stated themselves are
    added to the supported set so absent ride data never decays away something
    they told us directly. Writes are flushed, not committed — the caller owns the
    transaction. Returns the number of beliefs asserted.
    """
    metrics = await crud.get_rides_with_weather(db, user.id)
    rides = [ride_weather_from_metric(m) for m in metrics]
    beliefs = derive_weather_preferences(rides)

    supported: set[str] = set()
    for belief in beliefs:
        row = await crud.propose_athlete_hypothesis(
            db,
            user.id,
            statement=belief["statement"],
            category=belief["category"],
            rationale=belief["rationale"],
            confidence=belief["confidence"],
            evidence=belief["evidence"],
            alternative_explanations=belief["alternative_explanations"],
            observed_at=now,
        )
        supported.add(row.statement_key)

    existing = await crud.list_athlete_hypotheses(db, user.id, include_resolved=True)
    supported.update(
        row.statement_key
        for row in existing
        if row.category == CATEGORY and _is_athlete_stated(row)
    )

    await crud.decay_unsupported_model_hypotheses(
        db,
        user.id,
        category=CATEGORY,
        supported_keys=supported,
        now=now,
    )
    return len(beliefs)


async def heat_tolerance_for_user(
    db: AsyncSession,
    user_id: str,
) -> str | None:
    """This athlete's learned heat tolerance as a direction, or ``None``.

    Read by the freshness allocator (#602), which deducts utility from outdoor
    work on hot days and must not deduct it from an athlete who demonstrably
    rides fine in the heat — the failure this module exists to prevent, applied
    one layer up.

    Matched on the canonical statement text rather than a parsed field because
    that text *is* the merge key here: both ride evidence and what the athlete
    said themselves reinforce the same row through it.
    """
    by_statement = {
        _STATEMENTS[(DIMENSION_HEAT, direction)]: direction
        for direction in (DIRECTION_TOLERANT, DIRECTION_SENSITIVE)
    }
    rows = await crud.list_athlete_hypotheses(db, user_id)
    best: tuple[float, str] | None = None
    for row in rows:
        if row.category != CATEGORY:
            continue
        direction = by_statement.get(row.statement)
        if direction is None:
            continue
        confidence = float(row.confidence or 0.0)
        if best is None or confidence > best[0]:
            best = (confidence, direction)
    return best[1] if best is not None else None


async def weather_preference_context_for_user(
    db: AsyncSession,
    user_id: str,
) -> str:
    """Return the learned weather tolerances as a compact prompt section.

    Each belief is presented with its confidence and evidence count so the coach
    conditions its advice on what is actually known and can say "I think" where it
    only thinks — the uncertainty-revealing contract from #490.

    Only still-open (``proposed``) beliefs appear here. Once the athlete confirms
    one it is promoted into a durable memory fact by
    :func:`crud.update_athlete_hypothesis` and reaches the prompt through that
    channel instead, so repeating it here would state the same belief twice.
    """
    rows = await crud.list_athlete_hypotheses(db, user_id)
    beliefs = [row for row in rows if row.category == CATEGORY]
    if not beliefs:
        return ""

    lines = [
        "What is known about this athlete's own weather tolerances "
        "(confidence-scored beliefs, not facts — weight advice by the confidence "
        "and stay open about the uncertainty):"
    ]
    for row in beliefs:
        detail = f"(confidence {row.confidence:.2f}, {row.evidence_count} observation"
        detail += "s)" if row.evidence_count != 1 else ")"
        lines.append(f"- {row.statement} {detail}")
    return "\n".join(lines)
