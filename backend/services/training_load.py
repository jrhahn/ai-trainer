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
LOAD_SOURCE_RPE = "rpe"
LOAD_SOURCE_DURATION = "duration"

#: Rungs whose number came from a device rather than from an assumption.
MEASURED_LOAD_SOURCES = frozenset({LOAD_SOURCE_PROVIDER, LOAD_SOURCE_POWER})

#: Human-readable provenance, for prompts and anything else that shows a load.
LOAD_SOURCE_LABELS = {
    LOAD_SOURCE_PROVIDER: "provider-computed",
    LOAD_SOURCE_POWER: "from power",
    LOAD_SOURCE_HEART_RATE: "estimated from HR",
    LOAD_SOURCE_RPE: "from reported effort",
    LOAD_SOURCE_DURATION: "estimated from duration",
}

# How much the number can be trusted, which is not the same question as whether
# a device produced it (#712). Session-RPE is *reported* rather than measured,
# yet Foster's sRPE is the best-validated cross-sport load currency there is —
# better evidence than an assumed intensity per hour, and on a gym session
# better than a heart rate that says little about mechanical work. So the order
# here is the ladder's order, and "measured" is a separate axis.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

LOAD_SOURCE_CONFIDENCE = {
    LOAD_SOURCE_PROVIDER: CONFIDENCE_HIGH,
    LOAD_SOURCE_POWER: CONFIDENCE_HIGH,
    LOAD_SOURCE_HEART_RATE: CONFIDENCE_MEDIUM,
    LOAD_SOURCE_RPE: CONFIDENCE_MEDIUM,
    LOAD_SOURCE_DURATION: CONFIDENCE_LOW,
}

# What the number *is*. Everything has to end up on one scale or CTL/ATL cannot
# add it up, so every rung returns a TSS-equivalent — but only the top two are
# TSS in the sense the athlete's training software means it, and conflating the
# two is how a gym session's estimate gets discussed as though a power meter had
# been running (#579).
LOAD_UNIT_TSS = "tss"
LOAD_UNIT_TSS_EQUIVALENT = "tss_equivalent"

# Threshold heart rate as a fraction of maximum. Same ratio the FTP estimator
# already uses (``analysis.LTHR_RATIO``); duplicated as a local constant only to
# keep this module free of the import cycle into ``analysis``.
LTHR_FRACTION_OF_MAX = 0.87

# An HR-derived intensity above this is not a session, it is a bad reading — a
# dropout, a mis-paired strap, someone else's monitor. Cap rather than discard:
# the session still happened, we just refuse to let one artefact spike ATL.
MAX_HR_INTENSITY_FACTOR = 1.15

# The weakest rung: assumed load per hour when neither power nor heart rate said
# anything. Read as "an hour of this costs about as much as this many TSS of
# cycling would". Anything derived this way is labelled ``duration`` so the coach
# can discount it.
#
# The first pass at these (#579) was too timid, and production said so. A 58 min
# strength session came out at 34 against an ATL of 60, so TSB still *rose* 2,63
# points across it — less than the 5,85 it rose when the session counted as zero,
# but the same sign. The original numbers were chosen to be safe rather than
# right, and "safe" in this direction means under-reporting fatigue, which is the
# error that gets someone hurt.
#
# A real gym session is roughly a tempo hour in systemic cost, so strength is now
# 55 rather than 35: the same prod day then reads as flat, which is what an hour
# of hard training should look like on the freshness curve. Cycling and hiking
# move up for the same reason — an aerobic hour is not 45 TSS.
#
# These remain assumptions. The ladder above prefers a provider figure, then
# power, then heart rate, and only reaches this when all three said nothing; the
# ``duration`` label is what tells the coach it is holding an estimate.
DEFAULT_LOAD_PER_HOUR = {
    "cycling": 50.0,
    "running": 65.0,
    "strength": 55.0,
    "hike": 35.0,
    "walk": 20.0,
    "yoga": 20.0,
    "swim": 50.0,
    "swimming": 50.0,
    "rowing": 55.0,
}
FALLBACK_LOAD_PER_HOUR = 35.0


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

    @property
    def unit(self) -> str:
        return LOAD_UNIT_TSS if self.is_measured else LOAD_UNIT_TSS_EQUIVALENT

    @property
    def confidence(self) -> str:
        return LOAD_SOURCE_CONFIDENCE.get(self.source, CONFIDENCE_LOW)


@dataclass(frozen=True)
class LoadSignals:
    """Everything known about one session that could price it.

    A single argument rather than eight keywords because the list grows with
    every sport: running needs a pace model (#716), strength needs tonnage
    (#714), and a caller that has to be edited for each one is a caller that
    will be missed. Every field is optional — the point of the ladder is that it
    answers from whatever is present.
    """

    sport_type: str | None = None
    duration_seconds: float | int | None = None
    #: The provider's own load figure for this session, in its own sport.
    provider_load: float | int | None = None
    #: TSS from power against FTP. Only ever a cycling number (#711).
    power_tss: float | int | None = None
    avg_hr_bpm: float | int | None = None
    max_heart_rate: int | None = None
    resting_heart_rate: int | None = None
    #: The athlete's reported effort on this app's 1-5 scale.
    perceived_effort: float | int | None = None


def session_load(signals: LoadSignals) -> TrainingLoad | None:
    """Price one session from whatever is known about it, with its provenance.

    One abstraction for every sport (#712), replacing "TSS from a power stream or
    nothing". The ladder, strongest rung first:

    1. the provider's own figure, computed for the sport it actually was;
    2. TSS from power against FTP — cycling only, by #711;
    3. hrTSS from average heart rate;
    4. session-RPE from the athlete's reported effort (Foster);
    5. duration times an assumed intensity for the sport.

    A lower rung is only consulted when every rung above it had nothing to say,
    so deriving a load can never overwrite a measured one. ``None`` when not even
    a duration is known — then there genuinely is nothing to record, and the
    caller must leave the load unset rather than invent a zero, which is the
    claim that the athlete rested (#579).
    """
    provider = _positive(signals.provider_load)
    if provider is not None:
        return TrainingLoad(round(provider, 1), LOAD_SOURCE_PROVIDER)

    power = _positive(signals.power_tss)
    if power is not None:
        return TrainingLoad(round(power, 1), LOAD_SOURCE_POWER)

    from_hr = hr_training_load(
        duration_seconds=signals.duration_seconds,
        avg_hr_bpm=signals.avg_hr_bpm,
        max_heart_rate=signals.max_heart_rate,
        resting_heart_rate=signals.resting_heart_rate,
    )
    if from_hr is not None:
        return TrainingLoad(from_hr, LOAD_SOURCE_HEART_RATE)

    from_rpe = rpe_training_load(
        duration_seconds=signals.duration_seconds,
        perceived_effort=signals.perceived_effort,
    )
    if from_rpe is not None:
        return TrainingLoad(from_rpe, LOAD_SOURCE_RPE)

    from_duration = duration_training_load(
        duration_seconds=signals.duration_seconds,
        sport_type=signals.sport_type,
    )
    if from_duration is not None:
        return TrainingLoad(from_duration, LOAD_SOURCE_DURATION)

    return None


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


# Foster's session-RPE is RPE (CR10) × duration in minutes, in arbitrary units:
# an hour at a reported 10 is 600 AU. CTL/ATL are in TSS, where an hour at
# threshold is 100, so the two scales have to be reconciled before sRPE can
# enter the same chain.
#
# The two cannot be made to agree everywhere: sRPE is linear in effort and TSS
# is quadratic in intensity, so any single factor is exact at one point and
# wrong either side of it. The alignment is therefore placed at the *top* of the
# scale — an hour at a reported maximum is 110 TSS-equivalent, about what a
# genuinely maximal hour costs — because that is the end where being wrong
# matters. Anchoring at threshold instead made an hour at 5/5 come out at 143,
# a figure no hour of training has ever cost, and the whole point of this rung
# is to stop a session being mispriced.
#
# What falls out for the 1-5 scale an athlete actually uses: 22 / 44 / 66 / 88 /
# 110 TSS-equivalent per hour. The middle sits right next to the assumed
# per-hour figures below (cycling 50, running 65), which is the sanity check
# that matters — an athlete reporting a moderate hour and an athlete reporting
# nothing at all should not be priced differently by a factor of two.
#
# This is an alignment of two scales, not a conversion with physiological
# content, and the ``rpe`` label is what tells every reader so.
SRPE_MAX_HOUR_TSS_EQUIVALENT = 110.0
SRPE_AU_PER_TSS = (10.0 * 60.0) / SRPE_MAX_HOUR_TSS_EQUIVALENT

# The athlete's own effort scale in this app is 1-5 (``WorkoutLog``
# .perceived_effort, and the ``perceivedEffort`` on a plan day's feedback), not
# Foster's 0-10. Doubling is the same mapping ``ride_matching`` already uses in
# reverse when it reads an "RPE 8/10" out of a ride note and stores a 4.
RPE_SCALE_MAX = 5
CR10_PER_RPE_POINT = 2.0


def rpe_training_load(
    *,
    duration_seconds: float | int | None,
    perceived_effort: float | int | None,
) -> float | None:
    """Session-RPE (Foster), as a TSS-equivalent.

    The broadest validated cross-sport load currency there is, and the only rung
    that works for a session no device described: the athlete reports how hard an
    hour of lifting was, and that is real evidence rather than an assumption
    about what an hour of lifting usually costs.

    ``perceived_effort`` is on this app's 1-5 scale. ``None`` when either input
    is missing or the effort is outside the scale — a 0 or a 7 is a data-entry
    artefact, and silently clamping it would turn one into a load figure.
    """
    duration = _positive(duration_seconds)
    effort = _positive(perceived_effort)
    if duration is None or effort is None or effort > RPE_SCALE_MAX:
        return None
    cr10 = effort * CR10_PER_RPE_POINT
    session_au = cr10 * (duration / 60.0)
    return round(session_au / SRPE_AU_PER_TSS, 1)


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
    perceived_effort: float | int | None = None,
) -> TrainingLoad | None:
    """Keyword-argument face of :func:`session_load`, kept for its callers.

    The ladder itself lives in :func:`session_load`; this wraps it so the
    existing call sites — and the FTP-recompute and backfill paths that share
    the rule — need no change. New callers should build a :class:`LoadSignals`
    and call ``session_load`` directly, which is the shape that survives a sport
    gaining its own model.
    """
    return session_load(
        LoadSignals(
            sport_type=sport_type,
            duration_seconds=duration_seconds,
            provider_load=provider_tss,
            power_tss=power_tss,
            avg_hr_bpm=avg_hr_bpm,
            max_heart_rate=max_heart_rate,
            resting_heart_rate=resting_heart_rate,
            perceived_effort=perceived_effort,
        )
    )


def _estimate_provenance(source: str) -> str:
    """``"estimated from HR, medium confidence"`` — the model and its weight.

    Both halves, because naming the model is not enough on its own: a reader
    told only "estimated from duration" and "estimated from HR" has no way to
    know which of the two to discount, and they are not close. Only estimates
    carry this; a measured figure stays a bare ``TSS 94`` and costs no tokens
    (#712).
    """
    label = LOAD_SOURCE_LABELS.get(source, source)
    confidence = LOAD_SOURCE_CONFIDENCE.get(source)
    return f"{label}, {confidence} confidence" if confidence else label


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
    return f"load ~{value} ({_estimate_provenance(source)})"


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
    return f"Load: ~{value} ({_estimate_provenance(source)})"
