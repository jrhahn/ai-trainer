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
from services import limiter_detection

logger = logging.getLogger(__name__)

# Rolling window: how many recent rides to read and the nominal span reported as
# provenance. get_ride_metrics_history already returns newest-first, deduped rows.
PERF_WINDOW_RIDES = 60
PERF_WINDOW_DAYS = 120

# FTP is ~95% of a maximal 20-minute effort (the standard field-test factor).
_FTP_FROM_20MIN = 0.95
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
) -> dict[str, Any]:
    """Build one attribute dict in the persisted/schema shape."""
    return {
        "estimate": estimate,
        "score": score,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "unit": unit,
        "evidence": evidence or [],
        "missing_information": missing_information or [],
    }


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


def _infer_ftp(
    envelope: dict, ftp_used: int | None, recent_days: int | None
) -> tuple[dict, int | None]:
    best20 = envelope.get("20")
    if best20 and best20[0] > 0:
        watts, count, _ = best20
        estimate = round(watts * _FTP_FROM_20MIN)
        conf = _confidence(0.45, count, recent_days, cap=0.85)
        return (
            _attr(
                estimate=estimate,
                confidence=conf,
                unit="W",
                evidence=[
                    f"Best 20-min power {watts} W across {count} ride(s) "
                    f"(FTP ≈ 95% of a maximal 20-min effort)"
                ],
                missing_information=[]
                if count >= 2
                else ["Only one sustained 20-min effort in the window"],
            ),
            estimate,
        )
    if ftp_used:
        return (
            _attr(
                estimate=ftp_used,
                confidence=0.3,
                unit="W",
                evidence=["Carried from the FTP last used for load calculations"],
                missing_information=[
                    "No maximal ~20-min effort in the window to confirm FTP"
                ],
            ),
            ftp_used,
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
