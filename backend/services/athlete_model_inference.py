"""Deterministic cross-workout physiological inference engine (#476).

Populates the Athlete Performance Model (#475) by combining information across
*many* workouts rather than interpreting a single ride. It reads the compact
per-ride signals persisted on ``RideMetric.perf_signals`` (see
:func:`services.analysis.compute_ride_performance_signals`) over a rolling window
and produces, for every physiological attribute, an

    estimate / score  +  confidence  +  evidence  +  missing_information

tuple. The core is rule-based and pure (unit-testable); no attribute is ever
emitted without a confidence, and attributes with no supporting data are reported
as ``unknown`` at low confidence rather than guessed. An LLM is deliberately not
used here — the numbers must be reproducible and explainable.

Attributes produced: ``ftp``, ``map``, ``vo2max``, ``fractional_utilization``,
``aerobic_endurance``, ``fatigue_resistance`` and ``anaerobic_capacity`` for the
bike, plus ``critical_speed``, ``d_prime`` and ``threshold_pace`` for running
(#716) — fitted from the pace-duration envelope by ``services.run_model``, and
kept strictly apart from the power envelope, since the two describe different
thresholds and mixing them is the #711 error one level in. On
refresh, :mod:`services.limiter_detection` (#477) reads those attributes to fill
``likely_limiter`` and the ranked ``limiters`` list.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import analysis, limiter_detection, run_model

logger = logging.getLogger(__name__)

# Rolling window: how many recent rides to read and the nominal span reported as
# provenance. get_ride_metrics_history already returns newest-first, deduped rows.
PERF_WINDOW_RIDES = 60
PERF_WINDOW_DAYS = 120

# FTP inference reads the whole threshold band of the power-duration envelope
# (10-60 min) through the shared duration factors in services.analysis, not the
# 20-min point alone: in an interval session the rolling 20-min window straddles
# work and recovery, so 3 x 12 min at 340 W reads as ~275 W there and the
# resulting FTP implies the athlete held 130 % of threshold for 12 min (#604).
_FTP_BAND_MIN = 10.0
# An effort shorter than this cannot be a threshold *test*. Repeated intervals
# prove a floor — we never know they were maximal — so they may raise the
# estimate but not carry test-grade confidence.
_SUSTAINED_TEST_MIN = 20.0
# Athletes ride a repeated interval set below what they could hold once. Used
# only to widen the upper bound of the reported range, never the point estimate.
_REPEATED_EFFORT_SUBMAXIMAL = 0.95
# Confidence ceilings: a sustained test earns the historical cap, interval-only
# evidence much less, and an estimate the plausibility check had to correct
# least of all — the model just contradicted itself.
_SUSTAINED_CONFIDENCE_CAP = 0.85
_INTERVAL_ONLY_CONFIDENCE_CAP = 0.6
_CORRECTED_CONFIDENCE_CAP = 0.5
# A range wider than this (high/low) means the candidates disagree materially.
_WIDE_RANGE_RATIO = 1.05
_WIDE_RANGE_PENALTY = 0.1
# MAP (maximal aerobic power) is best approximated by a maximal 4-6 min effort.
_MAP_DURATION_KEYS = ("5", "4", "6")
# VO2max (ml/kg/min) from MAP in W/kg — a common linear field estimate.
_VO2_SLOPE = 10.8
_VO2_INTERCEPT = 7.0

# Duration thresholds (seconds).
_LONG_RIDE_S = 9000  # 2.5 h — aerobic endurance / durability evidence
_DURABILITY_RIDE_S = 5400  # 1.5 h — fatigue-resistance evidence

# The running counterpart, and deliberately shorter (#718). A ride does not ask
# the tissue for anything until it is long; a run's durability question bites
# earlier, because every stride is an eccentric contraction and the cost
# accumulates from the first kilometre. An hour is where a recreational runner's
# long run sits, and demanding 1.5 h of them would leave this attribute unknown
# for everyone who is not training for a marathon.
_DURABILITY_RUN_S = 3600  # 1 h

# Which probed duration stands in for the velocity at VO₂max. vVO₂max is the
# slowest speed that elicits VO₂max, and time to exhaustion at it is ~6 min
# (Billat & Koralsztein 1996); 5 min is the nearest duration the run envelope
# probes (#716's ``RUN_SIGNAL_DURATIONS_MIN``).
#
# Five minutes is faster than six, so this **overestimates** vVO₂max, which
# makes the CS/vVO₂max ratio read lower than it is and biases the running limiter
# towards "threshold-limited" rather than "ceiling-limited". That is the safer
# direction by some distance: being wrongly told to work on Critical Speed costs
# a runner some tempo work, while being wrongly told to work on vVO₂max puts
# them on a track doing the highest-injury-risk sessions in the sport.
_VVO2MAX_DURATION_MIN = 5.0


def _attr(
    *,
    estimate: float | None = None,
    score: str | None = None,
    confidence: float,
    unit: str | None = None,
    evidence: list[str] | None = None,
    missing_information: list[str] | None = None,
    estimate_low: float | None = None,
    estimate_high: float | None = None,
    validation_protocol: str | None = None,
) -> dict[str, Any]:
    """Build one attribute dict in the persisted/schema shape.

    ``estimate_low``/``estimate_high`` bound a quantitative estimate when the
    evidence supports a range rather than a point, and ``validation_protocol``
    carries the test that would settle it. All three are omitted (rather than
    stored as ``None``) when they do not apply, so an attribute that is simply
    known keeps the compact shape it has always had.
    """
    attr: dict[str, Any] = {
        "estimate": estimate,
        "score": score,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "unit": unit,
        "evidence": evidence or [],
        "missing_information": missing_information or [],
    }
    if estimate_low is not None:
        attr["estimate_low"] = estimate_low
    if estimate_high is not None:
        attr["estimate_high"] = estimate_high
    if validation_protocol:
        attr["validation_protocol"] = validation_protocol
    return attr


def _confidence(base: float, count: int, recent_days: int | None, cap: float) -> float:
    """Scale confidence by evidence quantity and recency, bounded by ``cap``."""
    c = base + min(count, 5) * 0.08
    if recent_days is not None:
        if recent_days <= 21:
            c += 0.10
        elif recent_days > 60:
            c -= 0.10
    return max(0.05, min(cap, c))


def _days_since(day_str: str | None, ref: date) -> int | None:
    if not day_str:
        return None
    try:
        return (ref - date.fromisoformat(day_str)).days
    except ValueError:
        return None


def _power_envelope(
    signals: list[tuple[models.RideMetric, dict]],
) -> dict[str, tuple[int, int, str | None]]:
    """Best power per standard duration across the window.

    Returns ``{duration_key: (best_watts, contributing_ride_count, best_date)}``.
    """
    envelope: dict[str, tuple[int, int, str | None]] = {}
    for metric, sig in signals:
        curve = sig.get("power_curve") or {}
        for key, watts in curve.items():
            if not isinstance(watts, (int, float)):
                continue
            best, count, best_date = envelope.get(key, (0, 0, None))
            new_best, new_date = (
                (int(watts), metric.activity_date)
                if watts > best
                else (best, best_date)
            )
            envelope[key] = (new_best, count + 1, new_date)
    return envelope


def _decoupling_pct(sig: dict) -> float | None:
    """Power:HR decoupling (%) between first and second half of a ride.

    Positive means efficiency dropped in the second half (aerobic drift); lower is
    more durable. Returns ``None`` when the halves lack power or HR.
    """
    fp, sp = sig.get("first_half_power"), sig.get("second_half_power")
    fh, sh = sig.get("first_half_hr"), sig.get("second_half_hr")
    if not all(isinstance(x, (int, float)) and x for x in (fp, sp, fh, sh)):
        return None
    first_ratio = fp / fh
    second_ratio = sp / sh
    if first_ratio == 0:
        return None
    return (first_ratio - second_ratio) / first_ratio * 100.0


def _power_curve(envelope: dict) -> dict[str, int]:
    """Flatten the envelope to ``{duration_key: best_watts}`` for the sanity check."""
    return {key: point[0] for key, point in envelope.items() if point[0] > 0}


def _ftp_candidates(envelope: dict) -> list[tuple[float, int, int, int]]:
    """FTP candidates from every threshold-band point of the envelope.

    Returns ``(minutes, watts, ride_count, ftp_estimate)`` per duration, using the
    duration factors shared with the ride-level estimator. Each factor is < 1, so
    no candidate can ever equal the raw power of the effort behind it.
    """
    candidates: list[tuple[float, int, int, int]] = []
    for minutes, factor in analysis.FTP_POWER_DURATION_FACTORS:
        if minutes < _FTP_BAND_MIN:
            continue
        point = envelope.get(str(int(minutes)))
        if not point or point[0] <= 0:
            continue
        watts, count, _ = point
        candidates.append((minutes, watts, count, round(watts * factor)))
    return candidates


def _ftp_validation_protocol(estimate: int) -> str:
    """The structured test that would settle an uncertain FTP estimate.

    Carries the power the current estimate predicts for the test, so the athlete
    runs a falsifiable experiment rather than an open-ended effort.
    """
    factor = dict(analysis.FTP_POWER_DURATION_FACTORS)[_SUSTAINED_TEST_MIN]
    return (
        "20-minute threshold test: two easy days first, then 15 min warm-up, one "
        "5-min hard opener, 10 min easy, then 20 min all-out at an even pace on a "
        f"steady climb or the trainer. FTP is ~{factor:.0%} of the 20-min average "
        f"power. The current estimate predicts about {round(estimate / factor)} W "
        "for that 20 min — holding clearly more means FTP is higher than modelled."
    )


def _finalise_ftp(
    *,
    estimate: int,
    curve: dict,
    confidence: float,
    cap: float,
    evidence: list[str],
    missing: list[str],
    sustained: bool,
) -> tuple[dict, int]:
    """Sanity-check an FTP estimate against the curve it came from, then bound it.

    Shared by both estimating branches so a carried-forward FTP is checked exactly
    as hard as an inferred one. Three things happen here, in order:

    1. The estimate is compared against every recorded effort. If any effort would
       require a fraction of FTP nobody sustains for that duration, the estimate is
       raised to the *lowest* FTP that makes the athlete's own rides possible —
       never to the raw interval power — and confidence is cut, because the model
       having contradicted itself is not a reason to be more sure.
    2. The estimate is bounded: the low end is that same physiological floor, the
       high end allows for interval evidence being sub-maximal.
    3. When the result stays uncertain, a validation protocol is attached instead
       of the number being asserted.
    """
    conflict = analysis.check_ftp_against_power_curve(estimate, curve)
    if conflict and conflict.minimum_consistent_ftp > estimate:
        evidence = [
            *evidence,
            "Raised to the lowest FTP consistent with recent efforts: "
            + "; ".join(conflict.conflicts),
        ]
        missing = [
            *missing,
            "The estimate was corrected by the plausibility check rather than "
            "measured — the efforts behind it were not a threshold test, so the "
            "true FTP may be higher still",
        ]
        estimate = conflict.minimum_consistent_ftp
        cap = min(cap, _CORRECTED_CONFIDENCE_CAP)
        sustained = False

    low = min(analysis.minimum_consistent_ftp(curve) or estimate, estimate)
    high = estimate if sustained else round(estimate / _REPEATED_EFFORT_SUBMAXIMAL)
    if not sustained:
        missing = [
            *missing,
            "No maximal 20-min-or-longer effort in the window — repeated intervals "
            "establish a floor for FTP, not FTP itself",
        ]

    # The spread penalty is applied after the cap so it always bites: an estimate
    # whose own bounds disagree is less certain than one that does not, whatever
    # the quantity of evidence behind it.
    confidence = min(cap, confidence)
    if low > 0 and high / low > _WIDE_RANGE_RATIO:
        confidence -= _WIDE_RANGE_PENALTY
    confidence = max(0.05, confidence)

    return (
        _attr(
            estimate=estimate,
            confidence=confidence,
            unit="W",
            evidence=evidence,
            missing_information=missing,
            estimate_low=low,
            estimate_high=high,
            # No threshold test in the window (or an estimate the check had to
            # correct) is precisely the uncertainty a test would remove.
            validation_protocol=(
                None if sustained else _ftp_validation_protocol(estimate)
            ),
        ),
        estimate,
    )


def _infer_ftp(
    envelope: dict, ftp_used: int | None, recent_days: int | None
) -> tuple[dict, int | None]:
    curve = _power_curve(envelope)
    candidates = _ftp_candidates(envelope)
    if candidates:
        # The strongest candidate wins: FTP is a capability, and a rolling window
        # diluted by recovery understates it while a hard interval does not.
        minutes, watts, count, estimate = max(candidates, key=lambda c: c[3])
        return _finalise_ftp(
            estimate=estimate,
            curve=curve,
            confidence=_confidence(0.45, count, recent_days, cap=1.0),
            cap=_SUSTAINED_CONFIDENCE_CAP
            if minutes >= _SUSTAINED_TEST_MIN
            else _INTERVAL_ONLY_CONFIDENCE_CAP,
            evidence=[
                f"Best {minutes:g}-min power {watts} W across {count} ride(s) "
                f"(FTP ≈ {dict(analysis.FTP_POWER_DURATION_FACTORS)[minutes]:.0%} "
                f"of a maximal {minutes:g}-min effort)"
            ],
            missing=[]
            if count >= 2
            else [f"Only one {minutes:g}-min effort of this level in the window"],
            sustained=minutes >= _SUSTAINED_TEST_MIN,
        )
    if ftp_used:
        return _finalise_ftp(
            estimate=ftp_used,
            curve=curve,
            confidence=0.3,
            cap=0.3,
            evidence=["Carried from the FTP last used for load calculations"],
            missing=["No sustained effort in the window to confirm FTP"],
            sustained=False,
        )
    return (
        _attr(
            score="unknown",
            confidence=0.05,
            unit="W",
            missing_information=["No sustained efforts to estimate FTP"],
        ),
        None,
    )


def _infer_map(envelope: dict, recent_days: int | None) -> tuple[dict, int | None]:
    for key in _MAP_DURATION_KEYS:
        point = envelope.get(key)
        if point and point[0] > 0:
            watts, count, _ = point
            conf = _confidence(0.4, count, recent_days, cap=0.8)
            return (
                _attr(
                    estimate=watts,
                    confidence=conf,
                    unit="W",
                    evidence=[
                        f"Best {key}-min power {watts} W across {count} ride(s) "
                        "(maximal aerobic power)"
                    ],
                    missing_information=[]
                    if count >= 2
                    else ["Few maximal 4-6 min efforts in the window"],
                ),
                watts,
            )
    return (
        _attr(
            score="unknown",
            confidence=0.05,
            unit="W",
            missing_information=["No maximal 4-6 min efforts to estimate MAP"],
        ),
        None,
    )


def _infer_vo2max(map_watts: int | None, weight_kg: float | None) -> dict:
    if map_watts and weight_kg:
        vo2 = _VO2_SLOPE * (map_watts / weight_kg) + _VO2_INTERCEPT
        return _attr(
            estimate=round(vo2, 1),
            confidence=0.4,
            unit="ml/kg/min",
            evidence=[
                f"Estimated from MAP {map_watts} W at {weight_kg:g} kg "
                f"({map_watts / weight_kg:.2f} W/kg)"
            ],
            missing_information=[
                "Field estimate from power, not laboratory gas-exchange testing"
            ],
        )
    missing = ["No MAP estimate available"] if not map_watts else []
    if not weight_kg:
        missing.append("Body weight not recorded (VO2max needs ml/kg/min)")
    return _attr(
        score="unknown",
        confidence=0.05,
        unit="ml/kg/min",
        missing_information=missing,
    )


def _infer_fractional_utilization(
    ftp: int | None, map_watts: int | None
) -> dict:
    if ftp and map_watts and map_watts > 0:
        ratio = ftp / map_watts
        return _attr(
            estimate=round(ratio, 2),
            confidence=0.45,
            evidence=[
                f"FTP {ftp} W / MAP {map_watts} W — share of maximal aerobic "
                "power sustainable at threshold"
            ],
            missing_information=["Bounded by the FTP and MAP estimate confidence"],
        )
    return _attr(
        score="unknown",
        confidence=0.05,
        missing_information=["Needs both an FTP and a MAP estimate"],
    )


def _infer_aerobic_endurance(
    signals: list[tuple[models.RideMetric, dict]],
) -> dict:
    long_rides = [
        sig for _, sig in signals if (sig.get("duration_s") or 0) >= _LONG_RIDE_S
    ]
    if not long_rides:
        return _attr(
            score="unknown",
            confidence=0.1,
            missing_information=["No long (2.5 h+) rides to assess aerobic endurance"],
        )
    decouplings = [d for sig in long_rides if (d := _decoupling_pct(sig)) is not None]
    if not decouplings:
        return _attr(
            score="developing",
            confidence=0.2,
            evidence=[f"{len(long_rides)} long ride(s) (2.5 h+) completed"],
            missing_information=[
                "No heart-rate on the long rides to measure aerobic decoupling"
            ],
        )
    best = min(decouplings)
    if best < 5:
        score = "high"
    elif best < 8:
        score = "above_average"
    elif best < 12:
        score = "moderate"
    else:
        score = "developing"
    return _attr(
        score=score,
        confidence=_confidence(0.45, len(decouplings), None, cap=0.8),
        evidence=[
            f"{len(long_rides)} long ride(s) (2.5 h+); best power:HR decoupling "
            f"{best:.1f}% (lower = more durable aerobic base)"
        ],
        missing_information=[]
        if len(decouplings) >= 2
        else ["Only one long ride with usable HR"],
    )


def _infer_fatigue_resistance(
    signals: list[tuple[models.RideMetric, dict]],
) -> dict:
    ratios: list[float] = []
    for _, sig in signals:
        if (sig.get("duration_s") or 0) < _DURABILITY_RIDE_S:
            continue
        fp, sp = sig.get("first_half_power"), sig.get("second_half_power")
        if isinstance(fp, (int, float)) and isinstance(sp, (int, float)) and fp:
            ratios.append(sp / fp)
    if not ratios:
        return _attr(
            score="unknown",
            confidence=0.1,
            missing_information=[
                "No sustained (1.5 h+) rides to compare early vs late power"
            ],
        )
    avg = sum(ratios) / len(ratios)
    if avg >= 0.98:
        score = "high"
    elif avg >= 0.94:
        score = "above_average"
    elif avg >= 0.88:
        score = "moderate"
    else:
        score = "fades"
    return _attr(
        score=score,
        confidence=_confidence(0.4, len(ratios), None, cap=0.75),
        evidence=[
            f"Second-half vs first-half power held at {avg * 100:.0f}% across "
            f"{len(ratios)} long ride(s) (little drop = durable)"
        ],
    )


def _infer_anaerobic_capacity(envelope: dict, ftp: int | None) -> dict:
    one_min = envelope.get("1")
    if one_min and one_min[0] > 0 and ftp:
        watts, count, _ = one_min
        ratio = watts / ftp
        # We cannot know a 1-min effort was maximal, so confidence stays low.
        score = "above_average" if ratio >= 1.5 else "moderate"
        return _attr(
            score=score,
            confidence=0.3,
            evidence=[
                f"Best 1-min power {watts} W ({ratio:.1f}× FTP) across "
                f"{count} ride(s)"
            ],
            missing_information=[
                "Cannot confirm the 1-min efforts were maximal (no test protocol)"
            ],
        )
    return _attr(
        score="unknown",
        confidence=0.1,
        missing_information=["No short (~1 min) maximal efforts recorded"],
    )


def _speed_envelope(
    signals: list[tuple[models.RideMetric, dict]],
) -> tuple[dict[float, tuple[float, int, str | None]], int]:
    """Best mean speed per probed duration across the window, grade-adjusted.

    Returns ``({minutes: (best_m_s, run_count_at_that_duration, best_date)},
    runs_that_contributed_anything)``. The running counterpart of
    :func:`_power_envelope`, with three differences that are not cosmetic: the
    key is a number rather than a string, because the Critical Speed fit does
    arithmetic with it; it reads the grade-adjusted curve first, because a hilly
    maximal effort is a maximal effort and the raw curve would read it as a slow
    one and leave the athlete's best work out of the fit; and the number of
    contributing runs is counted for the envelope as a whole rather than taken
    from one duration's tally. Two runs that are each strongest at a different
    duration contribute one point apiece, and reporting "1 run" for a fit built
    from both would understate the evidence.
    """
    envelope: dict[float, tuple[float, int, str | None]] = {}
    contributors: set[int] = set()
    for metric, sig in signals:
        curve = sig.get("gap_speed_curve") or sig.get("speed_curve") or {}
        if not isinstance(curve, dict):
            continue
        for key, speed in curve.items():
            if not isinstance(speed, (int, float)) or isinstance(speed, bool):
                continue
            try:
                minutes = float(key)
            except (TypeError, ValueError):
                continue
            best, count, best_date = envelope.get(minutes, (0.0, 0, None))
            new_best, new_date = (
                (float(speed), metric.activity_date)
                if speed > best
                else (best, best_date)
            )
            envelope[minutes] = (new_best, count + 1, new_date)
            contributors.add(id(metric))
    return envelope, len(contributors)


def _infer_velocity_at_vo2max(
    envelope: dict[float, tuple[float, int, str | None]],
    recent_days: int | None,
) -> dict:
    """The aerobic *ceiling* for running, from the short end of the envelope (#718).

    The running analogue of :func:`_infer_map`, and the attribute the running
    limiter needs that #716 did not produce: Critical Speed says what the athlete
    can hold, and nothing about how much room there is above it. A runner whose CS
    sits just under their ceiling and one whose CS is far below it need opposite
    training, and without this the chain cannot tell them apart.

    Reported at low confidence on purpose. We cannot know a 5-minute effort in
    training was maximal — the same reservation
    :func:`_infer_anaerobic_capacity` states for a 1-minute power — and
    :data:`_VVO2MAX_DURATION_MIN` is a stand-in for a duration the envelope does
    not probe.
    """
    point = envelope.get(_VVO2MAX_DURATION_MIN)
    if not point or point[0] <= 0:
        return _attr(
            score="unknown",
            confidence=0.1,
            missing_information=[
                f"No {_VVO2MAX_DURATION_MIN:g}-minute run effort in the window to "
                "read the aerobic ceiling from"
            ],
            validation_protocol=(
                "A maximal 5-minute run on a track or flat road; the average speed "
                "is close to the velocity at VO₂max"
            ),
        )
    speed, count, _best_date = point
    # Base and cap both sit below :func:`_infer_map`'s 0.4/0.8. It is the same
    # kind of evidence — a measured point off the envelope — but read at a
    # substituted duration from an effort nobody verified was maximal, and those
    # are two reasons to be less sure rather than one. Not lower still: a ceiling
    # that could never clear the limiter gate would make the running limiter
    # unreachable, which is a different way of saying nothing.
    return _attr(
        estimate=round(speed, 3),
        unit="m/s",
        confidence=_confidence(0.3, count, recent_days, cap=0.6),
        evidence=[
            f"Best {_VVO2MAX_DURATION_MIN:g}-min run speed "
            f"{run_model.format_pace(speed)} across {count} run(s), taken as the "
            "velocity at VO₂max"
        ],
        missing_information=[
            "A 5-minute training effort may not have been maximal, and 5 min is "
            "shorter than the ~6 min vVO₂max is defined at, so this reads slightly "
            "fast"
        ],
    )


def _infer_run_fractional_utilization(
    critical_speed: float | None, vvo2max: float | None
) -> dict:
    """Critical Speed as a fraction of the running aerobic ceiling (#718).

    The running analogue of FTP/MAP, and the number the running limiter turns on.
    A well-developed Critical Speed sits near 90 % of vVO₂max (Jones &
    Vanhatalo 2017); far below that there is room to raise CS, and close to the
    ceiling it is the ceiling that has to move.
    """
    if not critical_speed or not vvo2max or vvo2max <= 0:
        return _attr(
            score="unknown",
            confidence=0.1,
            missing_information=[
                "Needs both a Critical Speed fit and a 5-minute maximal run effort"
            ],
        )
    # The same 0.45 as the cycling ratio, deliberately: a ratio is no more
    # trustworthy than the two estimates it divides, and
    # ``limiter_detection._min_input_confidence`` is what enforces that bound
    # downstream. Picking a different number here would be claiming the running
    # ratio is worse than the cycling one for a reason other than its inputs,
    # which is already priced in through them.
    return _attr(
        estimate=round(critical_speed / vvo2max, 3),
        confidence=0.45,
        evidence=[
            f"Critical Speed {run_model.format_pace(critical_speed)} against a "
            f"{run_model.format_pace(vvo2max)} aerobic ceiling"
        ],
        missing_information=[
            "Bounded by the Critical Speed and aerobic-ceiling estimate confidence"
        ],
    )


def _infer_run_fatigue_resistance(
    signals: list[tuple[models.RideMetric, dict]],
) -> dict:
    """Whether pace holds through a long run (#718).

    The running analogue of :func:`_infer_fatigue_resistance`, on the half-split
    speeds #716 began storing. The bands are **tighter** than the cycling ones
    and that is not a transcription slip: a runner's speed varies far less than a
    cyclist's power, because there is no coasting and the hills are already taken
    out by grade adjustment. Reused cycling bands would score every run "high"
    and the attribute would never say anything.
    """
    ratios: list[float] = []
    for _, sig in signals:
        if (sig.get("duration_s") or 0) < _DURABILITY_RUN_S:
            continue
        first, second = sig.get("first_half_speed"), sig.get("second_half_speed")
        if (
            isinstance(first, (int, float))
            and isinstance(second, (int, float))
            and first
        ):
            ratios.append(second / first)
    if not ratios:
        return _attr(
            score="unknown",
            confidence=0.1,
            missing_information=[
                f"No runs of {_DURABILITY_RUN_S // 60} min or more to compare early "
                "vs late pace"
            ],
        )
    avg = sum(ratios) / len(ratios)
    if avg >= 0.99:
        score = "high"
    elif avg >= 0.97:
        score = "above_average"
    elif avg >= 0.94:
        score = "moderate"
    else:
        score = "fades"
    return _attr(
        score=score,
        confidence=_confidence(0.4, len(ratios), None, cap=0.75),
        evidence=[
            f"Second-half vs first-half pace held at {avg * 100:.0f}% across "
            f"{len(ratios)} long run(s)"
        ],
    )


def _infer_run_attributes(
    signals: list[tuple[models.RideMetric, dict]],
    recent_days: int | None,
) -> dict[str, dict]:
    """Critical Speed, D′ and threshold pace from the run envelope (#716).

    Three attributes from one fit, because they answer three questions: CS is
    the aerobic ceiling, D′ is the finite distance above it (the kick), and
    threshold pace is the reference every rTSS figure and every pace zone is cut
    from — which is why it is reported separately and read back by
    ``metrics_service.get_effective_threshold_pace`` rather than recomputed
    wherever it is needed.

    When the envelope cannot support a fit, all three are reported ``unknown``
    at low confidence with what is missing named. That is the running side of
    the discipline the cycling attributes already keep: an invented Critical
    Speed would set the athlete's zones and their whole load history, and it
    would be indistinguishable from a measured one.
    """
    if not signals:
        return {}

    envelope, run_count = _speed_envelope(signals)
    points = {minutes: point[0] for minutes, point in envelope.items() if point[0] > 0}
    fit = run_model.critical_speed_from_points(points)

    # The ceiling and the durability score do not depend on the fit, so they are
    # reported even when Critical Speed is refused (#718). A runner whose pace
    # collapses in the second half of every long run has a durability limiter
    # whether or not their envelope can support a CS estimate, and withholding
    # the whole running model because one attribute of it is unknown is how a
    # sport ends up invisible to the chain.
    vvo2max_attr = _infer_velocity_at_vo2max(envelope, recent_days)
    extra = {
        "velocity_at_vo2max": vvo2max_attr,
        "run_fatigue_resistance": _infer_run_fatigue_resistance(signals),
    }

    if fit is None:
        missing = [
            "No maximal run efforts spanning "
            f"{run_model.MIN_CS_SPAN_MINUTES:g}+ minutes "
            f"({run_model.MIN_CS_POINTS} points needed between "
            f"{min(run_model.CRITICAL_SPEED_DURATIONS):g} and "
            f"{max(run_model.CRITICAL_SPEED_DURATIONS):g} min)"
        ]
        unknown = _attr(
            score="unknown",
            confidence=0.1,
            missing_information=missing,
            validation_protocol=(
                "Two maximal time trials on separate days — 3 min and 12 min — "
                "fit Critical Speed and D′ directly"
            ),
        )
        return {
            "critical_speed": unknown,
            "d_prime": dict(unknown),
            "threshold_pace": dict(unknown),
            "run_fractional_utilization": _infer_run_fractional_utilization(None, None),
            **extra,
        }

    confidence = run_model.critical_speed_confidence(fit, recent_days)
    evidence = [
        f"Critical Speed {run_model.format_pace(fit.speed_m_s)} fitted from "
        f"{fit.points_used} envelope points spanning {fit.span_minutes:g} min "
        f"across {run_count} run(s)",
        f"Worst fit residual {fit.max_residual:.1%} of speed",
    ]
    missing = [
        "Efforts were taken from training, not from a time-trial protocol, so "
        "they may not have been maximal"
    ]
    return {
        "critical_speed": _attr(
            estimate=fit.speed_m_s,
            unit="m/s",
            confidence=confidence,
            evidence=evidence,
            missing_information=missing,
        ),
        "d_prime": _attr(
            estimate=fit.d_prime_m,
            unit="m",
            confidence=confidence,
            evidence=[
                f"D′ {fit.d_prime_m:.0f} m — the distance available above "
                f"Critical Speed, from the same fit"
            ],
            missing_information=missing,
        ),
        "threshold_pace": _attr(
            estimate=round(fit.threshold_pace_seconds_per_km, 1),
            unit="s/km",
            confidence=confidence,
            evidence=[
                f"Threshold pace {run_model.format_pace(fit.threshold_speed_m_s)}, "
                f"{run_model.THRESHOLD_FRACTION_OF_CRITICAL_SPEED:.0%} of Critical "
                f"Speed"
            ],
            missing_information=missing,
        ),
        "run_fractional_utilization": _infer_run_fractional_utilization(
            fit.speed_m_s, vvo2max_attr.get("estimate")
        ),
        **extra,
    }


def infer_performance_attributes(
    metrics: list[models.RideMetric],
    *,
    weight_kg: float | None = None,
    max_hr: int | None = None,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Infer every physiological attribute from a window of ride metrics.

    ``metrics`` is a newest-first list of :class:`RideMetric` (as returned by
    :func:`crud.get_ride_metrics_history`). Returns a dict keyed by attribute name;
    empty when no ride carries usable performance signals.
    """
    signals = [(m, m.perf_signals) for m in metrics if m.perf_signals]
    if not signals:
        return {}

    # One column holds both envelopes (#716), so the two models are separated
    # here rather than hoped apart. A run's blob carries no ``power_curve`` and
    # no ``first_half_power`` by construction, but relying on an absence is how
    # a footpod run ends up arguing about a cycling threshold the first time
    # someone stores a key for a good reason — so the split is explicit.
    ride_signals = [(m, s) for m, s in signals if not run_model.is_run_signals(s)]
    run_signals = [(m, s) for m, s in signals if run_model.is_run_signals(s)]

    # Recency per sport, because a cycling attribute's confidence is a statement
    # about when the athlete last *rode*. ``None`` when that sport contributed
    # nothing, which ``_confidence`` and ``critical_speed_confidence`` both read
    # as "no recency information" rather than as "today".
    ref = (now or datetime.now(timezone.utc)).date()
    ride_recent_days = (
        _days_since(ride_signals[0][0].activity_date, ref) if ride_signals else None
    )
    run_recent_days = (
        _days_since(run_signals[0][0].activity_date, ref) if run_signals else None
    )

    envelope = _power_envelope(ride_signals)
    ftp_used = next((m.ftp_used for m, _ in ride_signals if m.ftp_used), None)

    # Passed straight through, never ``x or y``: a ride from *today* is 0 days
    # old, which is falsy, so an ``or`` silently substitutes the other sport's
    # recency for the freshest evidence there is.
    ftp_attr, ftp_val = _infer_ftp(envelope, ftp_used, ride_recent_days)
    map_attr, map_val = _infer_map(envelope, ride_recent_days)

    attributes = {
        "ftp": ftp_attr,
        "map": map_attr,
        "vo2max": _infer_vo2max(map_val, weight_kg),
        "fractional_utilization": _infer_fractional_utilization(ftp_val, map_val),
        "aerobic_endurance": _infer_aerobic_endurance(ride_signals),
        "fatigue_resistance": _infer_fatigue_resistance(ride_signals),
        "anaerobic_capacity": _infer_anaerobic_capacity(envelope, ftp_val),
    }
    attributes.update(_infer_run_attributes(run_signals, run_recent_days))
    return attributes


async def refresh_performance_model(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
) -> models.AthletePerformanceModel | None:
    """Recompute and persist the Athlete Performance Model for ``user``.

    Best-effort and side-effect-safe: returns ``None`` (leaving any existing model
    untouched) when there is not enough signal to infer anything. Writes are
    flushed but not committed — the caller owns the transaction, matching the
    continuous-learning pipeline.
    """
    metrics = await crud.get_ride_metrics_history(db, user.id, limit=PERF_WINDOW_RIDES)
    attributes = infer_performance_attributes(
        metrics,
        weight_kg=None,  # body weight is not a stored column (#475); VO2max stays unknown
        max_hr=user.max_heart_rate,
        now=now,
    )
    if not attributes:
        return None

    # Limiter detection (#477) reads the freshly inferred attributes.
    limiters = limiter_detection.detect_limiters(attributes)
    likely_limiter = limiter_detection.top_limiter(limiters)

    derived_from = sum(1 for m in metrics if m.perf_signals)
    row = await crud.upsert_athlete_performance_model(
        db,
        user.id,
        attributes=attributes,
        likely_limiter=likely_limiter,
        limiters=limiters,
        source_window_days=PERF_WINDOW_DAYS,
        derived_from_rides=derived_from,
    )
    await crud.create_athlete_performance_snapshot(
        db,
        user.id,
        attributes=attributes,
        likely_limiter=likely_limiter,
        limiters=limiters,
        recorded_at=now,
    )
    return row
