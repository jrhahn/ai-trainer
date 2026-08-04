"""Deterministic physiological limiter detection (#477).

Reads the Athlete Performance Model attributes produced by the inference engine
(:mod:`services.athlete_model_inference`) and returns a **ranked** list of
candidate physiological limiters — the single most valuable coaching output:
*where is the athlete currently limited, and why?*

Each candidate carries ``confidence``, ``evidence`` and ``counter_evidence`` and
is never presented as fact. The core reasoning reproduces what a good human coach
does — "if the engine is already big, raise the floor":

- **threshold** — the aerobic ceiling (MAP/VO₂max) is well ahead of sustainable
  threshold power (low *fractional utilization*). There is headroom to raise FTP
  toward the ceiling, so threshold work is expected to pay off most.
- **vo2max** — threshold already sits close to the aerobic ceiling (high
  fractional utilization); further threshold work has little room, so raising
  MAP/VO₂max is the higher-return target.
- **endurance_durability** — power fades late in long rides / high cardiac drift.
- **anaerobic_capacity** — reserved; our data rarely confirms maximal short
  efforts, so it is only surfaced at low confidence.

Like the inference engine this is rule-based and pure (unit-testable). When the
signals are missing it returns a single low-confidence ``insufficient_data``
entry rather than guessing a limiter.
"""

from __future__ import annotations

from typing import Any

# Limiter identifiers written into AthletePerformanceModel.likely_limiter.
LIMITER_THRESHOLD = "threshold"
LIMITER_VO2MAX = "vo2max"
LIMITER_DURABILITY = "endurance_durability"
LIMITER_INSUFFICIENT = "insufficient_data"

# Fractional utilization = FTP / MAP. Well-developed threshold sits ~72-77% of
# maximal aerobic power; below that the ceiling is under-exploited (threshold
# limiter), well above it the ceiling itself constrains (VO₂max limiter).
FRAC_THRESHOLD_LIMITED = 0.72
FRAC_VO2_CEILING = 0.80

# Only promote a candidate to model.likely_limiter above this confidence.
LIKELY_LIMITER_MIN_CONFIDENCE = 0.35

# Ordinal ranking of the qualitative scores the inference engine emits.
_AEROBIC_ORDINAL = {"high": 4, "above_average": 3, "moderate": 2, "developing": 1}
_FATIGUE_ORDINAL = {"high": 4, "above_average": 3, "moderate": 2, "fades": 1}


def _limiter(
    name: str,
    confidence: float,
    evidence: list[str],
    counter_evidence: list[str],
) -> dict[str, Any]:
    """Build one ranked-limiter dict in the persisted/schema shape."""
    return {
        "limiter": name,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": evidence,
        "counter_evidence": counter_evidence,
    }


def _min_input_confidence(*attrs: dict | None) -> float:
    """Weakest confidence among the attributes a judgement is built on.

    A limiter can never be more certain than the least certain estimate feeding
    it, so this bounds the candidate's confidence.
    """
    confs = [
        a.get("confidence")
        for a in attrs
        if a and isinstance(a.get("confidence"), (int, float))
    ]
    return min(confs) if confs else 0.0


def _threshold_or_vo2_candidate(
    frac: dict, ftp: dict, mp: dict, vo2: dict, aer: dict, fat: dict
) -> dict | None:
    """Decide between a threshold and a VO₂max-ceiling limiter from the gap
    between the aerobic ceiling (MAP) and sustainable threshold power (FTP)."""
    frac_est = frac.get("estimate")
    ftp_est = ftp.get("estimate")
    map_est = mp.get("estimate")
    if frac_est is None or not ftp_est or not map_est:
        return None

    input_conf = _min_input_confidence(frac, ftp, mp)
    aer_ord = _AEROBIC_ORDINAL.get(aer.get("score"))
    fat_ord = _FATIGUE_ORDINAL.get(fat.get("score"))

    if frac_est < FRAC_THRESHOLD_LIMITED:
        signal = min(1.0, (FRAC_THRESHOLD_LIMITED - frac_est) / 0.12)
        evidence = [
            f"Threshold is only {frac_est:.0%} of maximal aerobic power "
            f"(FTP {ftp_est:g} W vs MAP {map_est:g} W); a well-developed threshold "
            "sits near 72-77% of MAP, so there is headroom to raise FTP toward "
            "the aerobic ceiling."
        ]
        counter: list[str] = []
        conf = input_conf * (0.55 + 0.45 * signal)
        if aer_ord is not None and aer_ord >= 3:
            evidence.append(
                f"Aerobic endurance looks {aer.get('score', '').replace('_', ' ')}, "
                "indicating a strong base to build threshold on."
            )
            conf += 0.10
        elif aer_ord is None:
            counter.append(
                "Aerobic base not yet confirmed (no long rides with usable HR), "
                "so the strong-engine assumption is unverified."
            )
            conf -= 0.05
        if fat_ord is not None and fat_ord <= 1:
            counter.append(
                "Power fades late in long rides, which points at durability "
                "rather than threshold."
            )
        return _limiter(LIMITER_THRESHOLD, conf, evidence, counter)

    if frac_est >= FRAC_VO2_CEILING:
        signal = min(1.0, (frac_est - FRAC_VO2_CEILING) / 0.10)
        evidence = [
            f"Threshold already reaches {frac_est:.0%} of maximal aerobic power "
            f"(FTP {ftp_est:g} W vs MAP {map_est:g} W); with threshold this close "
            "to the ceiling, raising MAP/VO₂max offers more headroom than more "
            "threshold work."
        ]
        counter = []
        conf = input_conf * (0.55 + 0.45 * signal)
        if vo2.get("estimate") is None:
            counter.append(
                "VO₂max itself is unknown (no body weight recorded), so the size "
                "of the aerobic ceiling is uncertain."
            )
            conf -= 0.05
        return _limiter(LIMITER_VO2MAX, conf, evidence, counter)

    return None


def _durability_candidate(aer: dict, fat: dict) -> dict | None:
    """Flag endurance durability when power fades late or long-ride drift is high."""
    aer_ord = _AEROBIC_ORDINAL.get(aer.get("score"))
    fat_ord = _FATIGUE_ORDINAL.get(fat.get("score"))
    weak_fatigue = fat_ord is not None and fat_ord <= 1  # "fades"
    weak_aerobic = aer_ord is not None and aer_ord <= 1  # "developing"
    if not (weak_fatigue or weak_aerobic):
        return None

    evidence: list[str] = []
    if weak_fatigue:
        evidence.append(
            f"Fatigue resistance scores '{fat.get('score')}' — power drops "
            "noticeably in the second half of long rides."
        )
    if weak_aerobic:
        evidence.append(
            f"Aerobic endurance scores '{aer.get('score')}' — high cardiac drift "
            "or limited long-ride durability."
        )
    counter: list[str] = []
    if aer_ord is not None and aer_ord >= 3:
        counter.append(
            "Aerobic endurance is otherwise strong, so the late fade may be "
            "situational (fuelling/heat) rather than a fitness limiter."
        )

    base_conf = _min_input_confidence(fat, aer) or 0.2
    severity = (0 if fat_ord is None else 2 - fat_ord) + (
        0 if aer_ord is None else 2 - aer_ord
    )
    conf = min(0.35 + 0.12 * severity, base_conf + 0.15, 0.7)
    return _limiter(LIMITER_DURABILITY, conf, evidence, counter)


def detect_limiters(attributes: dict[str, dict]) -> list[dict]:
    """Return a confidence-ranked list of candidate physiological limiters.

    ``attributes`` is the Athlete Performance Model attribute map (as produced by
    :func:`services.athlete_model_inference.infer_performance_attributes`). When no
    candidate can be supported, returns a single ``insufficient_data`` entry rather
    than guessing.
    """
    frac = attributes.get("fractional_utilization") or {}
    ftp = attributes.get("ftp") or {}
    mp = attributes.get("map") or {}
    vo2 = attributes.get("vo2max") or {}
    aer = attributes.get("aerobic_endurance") or {}
    fat = attributes.get("fatigue_resistance") or {}

    candidates: list[dict] = []
    primary = _threshold_or_vo2_candidate(frac, ftp, mp, vo2, aer, fat)
    if primary is not None:
        candidates.append(primary)
    durability = _durability_candidate(aer, fat)
    if durability is not None:
        candidates.append(durability)

    if not candidates:
        return [
            _limiter(
                LIMITER_INSUFFICIENT,
                0.05,
                [],
                [
                    "Not enough signal across the model's attributes to identify a "
                    "limiter (need FTP, MAP and long-ride data)."
                ],
            )
        ]

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates


def top_limiter(limiters: list[dict]) -> str | None:
    """The single most-probable limiter to store on ``likely_limiter``.

    Returns ``None`` (leaving the field unset) when the ranking is empty, is only
    ``insufficient_data``, or the top candidate is below
    :data:`LIKELY_LIMITER_MIN_CONFIDENCE` — we would rather say nothing than name a
    limiter we do not believe.
    """
    if not limiters:
        return None
    top = limiters[0]
    if (
        top["limiter"] == LIMITER_INSUFFICIENT
        or top["confidence"] < LIKELY_LIMITER_MIN_CONFIDENCE
    ):
        return None
    return top["limiter"]
