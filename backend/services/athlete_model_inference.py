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
``aerobic_endurance``, ``fatigue_resistance`` and ``anaerobic_capacity``. On
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
from services import analysis, limiter_detection

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

    ref = (now or datetime.now(timezone.utc)).date()
    # Recency of the window: days since the newest ride that carried signals.
    recent_days = _days_since(signals[0][0].activity_date, ref)

    envelope = _power_envelope(signals)
    ftp_used = next((m.ftp_used for m, _ in signals if m.ftp_used), None)

    ftp_attr, ftp_val = _infer_ftp(envelope, ftp_used, recent_days)
    map_attr, map_val = _infer_map(envelope, recent_days)

    return {
        "ftp": ftp_attr,
        "map": map_attr,
        "vo2max": _infer_vo2max(map_val, weight_kg),
        "fractional_utilization": _infer_fractional_utilization(ftp_val, map_val),
        "aerobic_endurance": _infer_aerobic_endurance(signals),
        "fatigue_resistance": _infer_fatigue_resistance(signals),
        "anaerobic_capacity": _infer_anaerobic_capacity(envelope, ftp_val),
    }


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
