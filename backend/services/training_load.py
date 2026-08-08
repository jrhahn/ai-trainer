"""How much load did this session carry, and where does that number come from?

``build_ride_metrics_chain`` used to know exactly one answer: TSS from a power
stream, or nothing. "Nothing" then entered the fitness/fatigue chain as ``0.0``
— which is not a gap in the data, it is the assertion that the athlete rested.
An hour of strength training therefore *raised* TSB, and the coach planned on
that number (#579).

The fix is a ladder, from measured to estimated, plus the provenance of every
figure. Provenance is not decoration: a load whose origin is not recorded cannot
be reasoned about later, and the coach must never report rising *cycling* form
on the back of gym work it cannot tell apart from a threshold session.

Pure module — no DB, no HTTP, no LLM — so the chain, the FTP-change recompute
and the backfill migration can all apply the same rule instead of three.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.activity_identity import activity_family

# The rungs, strongest first. Stored verbatim in ``ride_metrics.tss_source``.
LOAD_SOURCE_PROVIDER = "provider"
LOAD_SOURCE_POWER = "power"
LOAD_SOURCE_HEART_RATE = "heart_rate"
LOAD_SOURCE_DURATION = "duration"

#: Rungs whose number came from a device rather than from an assumption.
MEASURED_LOAD_SOURCES = frozenset({LOAD_SOURCE_PROVIDER, LOAD_SOURCE_POWER})

#: Human-readable provenance, for prompts and anything else that shows a load.
LOAD_SOURCE_LABELS = {
    LOAD_SOURCE_PROVIDER: "provider-computed",
    LOAD_SOURCE_POWER: "from power",
    LOAD_SOURCE_HEART_RATE: "estimated from HR",
    LOAD_SOURCE_DURATION: "estimated from duration",
}

# Threshold heart rate as a fraction of maximum. Same ratio the FTP estimator
# already uses (``analysis.LTHR_RATIO``); duplicated as a local constant only to
# keep this module free of the import cycle into ``analysis``.
LTHR_FRACTION_OF_MAX = 0.87

# An HR-derived intensity above this is not a session, it is a bad reading — a
# dropout, a mis-paired strap, someone else's monitor. Cap rather than discard:
# the session still happened, we just refuse to let one artefact spike ATL.
MAX_HR_INTENSITY_FACTOR = 1.15

# The weakest rung: assumed load per hour when neither power nor heart rate said
# anything. These are deliberately conservative — the purpose is to stop a
# session reading as a rest day, not to pretend we measured it. Anything derived
# this way is labelled ``duration`` so the coach can discount it.
DEFAULT_LOAD_PER_HOUR = {
    "cycling": 45.0,
    "running": 65.0,
    "strength": 35.0,
    "hike": 30.0,
    "walk": 20.0,
    "yoga": 20.0,
    "swim": 50.0,
    "swimming": 50.0,
    "rowing": 55.0,
}
FALLBACK_LOAD_PER_HOUR = 30.0


@dataclass(frozen=True)
class TrainingLoad:
    """A load figure and where it came from. Never one without the other."""

    tss: float
    source: str

    @property
    def is_measured(self) -> bool:
        return self.source in MEASURED_LOAD_SOURCES

    @property
    def label(self) -> str:
        return LOAD_SOURCE_LABELS.get(self.source, self.source)


def _positive(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def hr_training_load(
    *,
    duration_seconds: float | int | None,
    avg_hr_bpm: float | int | None,
    max_heart_rate: int | None,
    resting_heart_rate: int | None = None,
) -> float | None:
    """hrTSS: an hour at threshold heart rate is 100, quadratic below it.

    Intensity is measured against heart-rate *reserve* when a resting rate is
    known, because a fraction of maximum overstates easy work badly — 110 bpm of
    a 185 maximum reads as 59 % of max but only 44 % of reserve, and squaring
    the difference doubles the error. Without a resting rate we fall back to the
    fraction of maximum, which is what the athlete's profile usually has.

    ``None`` when there is nothing to work from: no duration, no average HR, no
    maximum, or an average at or below resting (a reading, not a session).
    """
    duration = _positive(duration_seconds)
    avg_hr = _positive(avg_hr_bpm)
    max_hr = _positive(max_heart_rate)
    if duration is None or avg_hr is None or max_hr is None:
        return None

    resting = _positive(resting_heart_rate)
    if resting is not None and resting < max_hr:
        reserve = max_hr - resting
        effort = (avg_hr - resting) / reserve
        threshold = (max_hr * LTHR_FRACTION_OF_MAX - resting) / reserve
    else:
        effort = avg_hr / max_hr
        threshold = LTHR_FRACTION_OF_MAX

    if effort <= 0 or threshold <= 0:
        return None

    intensity = min(effort / threshold, MAX_HR_INTENSITY_FACTOR)
    return round(duration / 3600.0 * intensity * intensity * 100.0, 1)


def duration_training_load(
    *,
    duration_seconds: float | int | None,
    sport_type: str | None,
) -> float | None:
    """Assumed load from time on task alone — the last rung before zero.

    Zero is a claim about the athlete ("you rested"); this is a claim about our
    data ("we know how long, not how hard"). The second is wrong by a margin,
    the first is wrong by a sign.
    """
    duration = _positive(duration_seconds)
    if duration is None:
        return None
    family = activity_family(sport_type)
    per_hour = DEFAULT_LOAD_PER_HOUR.get(family, FALLBACK_LOAD_PER_HOUR)
    return round(duration / 3600.0 * per_hour, 1)


def resolve_training_load(
    *,
    provider_tss: float | int | None = None,
    power_tss: float | int | None = None,
    duration_seconds: float | int | None = None,
    sport_type: str | None = None,
    avg_hr_bpm: float | int | None = None,
    max_heart_rate: int | None = None,
    resting_heart_rate: int | None = None,
) -> TrainingLoad | None:
    """Pick the best-supported load for one activity, with its provenance.

    The ladder, strongest rung first:

    1. the provider's own figure, when it computed one;
    2. TSS from power against FTP;
    3. hrTSS from average heart rate;
    4. duration times an assumed intensity for the sport.

    A lower rung is only ever consulted when every rung above it had nothing to
    say, so deriving a load can never overwrite a measured one. ``None`` when
    even the duration is missing — then there genuinely is nothing to record,
    and the caller should leave the load unset rather than invent a zero.
    """
    provider = _positive(provider_tss)
    if provider is not None:
        return TrainingLoad(round(provider, 1), LOAD_SOURCE_PROVIDER)

    power = _positive(power_tss)
    if power is not None:
        return TrainingLoad(round(power, 1), LOAD_SOURCE_POWER)

    from_hr = hr_training_load(
        duration_seconds=duration_seconds,
        avg_hr_bpm=avg_hr_bpm,
        max_heart_rate=max_heart_rate,
        resting_heart_rate=resting_heart_rate,
    )
    if from_hr is not None:
        return TrainingLoad(from_hr, LOAD_SOURCE_HEART_RATE)

    from_duration = duration_training_load(
        duration_seconds=duration_seconds,
        sport_type=sport_type,
    )
    if from_duration is not None:
        return TrainingLoad(from_duration, LOAD_SOURCE_DURATION)

    return None


def format_load(tss: float | int | None, source: str | None) -> str | None:
    """Render a load the way every prompt and summary should: never bare.

    ``TSS 94`` for a measured figure, ``load ~28 (estimated from HR)`` for one we
    derived. The tilde and the parenthetical are the whole point — a coach that
    cannot see which is which will talk about a gym session's "TSS" as though a
    power meter had been on the bike.
    """
    if tss is None:
        return None
    value = round(float(tss))
    if source is None or source in MEASURED_LOAD_SOURCES:
        return f"TSS {value}"
    label = LOAD_SOURCE_LABELS.get(source, source)
    return f"load ~{value} ({label})"


def format_load_field(tss: float | int | None, source: str | None) -> str | None:
    """The same figure for prompts written as ``Key: value`` lines.

    ``TSS: 94`` when it was measured, ``Load: ~28 (estimated from HR)`` when it
    was not — a different key, because calling an estimate "TSS" is precisely
    the conflation this exists to prevent.
    """
    if tss is None:
        return None
    value = round(float(tss))
    if source is None or source in MEASURED_LOAD_SOURCES:
        return f"TSS: {value}"
    label = LOAD_SOURCE_LABELS.get(source, source)
    return f"Load: ~{value} ({label})"
