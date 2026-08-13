"""Deterministic ROI-based training recommendation (#478).

Turns the Athlete Performance Model and its detected limiter (:mod:`services.
limiter_detection`) into an **expected-gain-per-physiological-system** map plus a
suggested weekly emphasis and a natural-language rationale that cites concrete
evidence from the model.

This replaces generic periodization ("Tuesday VO₂max, Thursday threshold") with a
recommendation that explains *why*: if the athlete's limiter is threshold, more
threshold work is expected to pay off more than another VO₂max block, and the
rationale says so in terms of the athlete's own numbers (FTP vs MAP, fractional
utilization, aerobic base).

Like the limiter and inference stages this is rule-based and pure — no LLM, no DB
— so it is fully unit-testable. When the model lacks a confident limiter it returns
``sufficient=False`` with a neutral, balanced emphasis so callers fall back to their
existing periodization rather than acting on a guess.
"""

from __future__ import annotations

from typing import Any

from services.limiter_detection import (
    LIMITER_DURABILITY,
    LIMITER_THRESHOLD,
    LIMITER_VO2MAX,
    top_limiter,
)

# Physiological systems a recommendation can emphasise.
SYSTEM_THRESHOLD = "threshold"
SYSTEM_VO2MAX = "vo2max"
SYSTEM_ENDURANCE = "endurance"
SYSTEM_ANAEROBIC = "anaerobic"

# Expected-gain buckets, coarse on purpose — the model is not precise enough to
# claim watt-level returns, so we speak in relative return-on-investment.
GAIN_LARGE = "large"
GAIN_MODERATE = "moderate"
GAIN_SMALL = "small"
GAIN_MAINTENANCE = "maintenance"

# Public: #602's day board ranks the same buckets when a kind of day could be
# delivered as either of two systems.
GAIN_RANK = {GAIN_LARGE: 3, GAIN_MODERATE: 2, GAIN_SMALL: 1, GAIN_MAINTENANCE: 0}

_SYSTEM_LABEL = {
    SYSTEM_THRESHOLD: "Threshold",
    SYSTEM_VO2MAX: "VO₂max",
    SYSTEM_ENDURANCE: "Long endurance",
    SYSTEM_ANAEROBIC: "Anaerobic",
}

# Per-limiter expected gain across systems. The limiter is the highest-return
# target; the rest fall off from there. Endurance stays "maintenance" whenever it
# is not the limiter — the aerobic base is preserved, not the priority.
_GAIN_BY_LIMITER: dict[str, dict[str, str]] = {
    LIMITER_THRESHOLD: {
        SYSTEM_THRESHOLD: GAIN_LARGE,
        SYSTEM_VO2MAX: GAIN_SMALL,
        SYSTEM_ENDURANCE: GAIN_MAINTENANCE,
        SYSTEM_ANAEROBIC: GAIN_SMALL,
    },
    LIMITER_VO2MAX: {
        SYSTEM_VO2MAX: GAIN_LARGE,
        SYSTEM_THRESHOLD: GAIN_MODERATE,
        SYSTEM_ENDURANCE: GAIN_MAINTENANCE,
        SYSTEM_ANAEROBIC: GAIN_SMALL,
    },
    LIMITER_DURABILITY: {
        SYSTEM_ENDURANCE: GAIN_LARGE,
        SYSTEM_THRESHOLD: GAIN_MODERATE,
        SYSTEM_VO2MAX: GAIN_SMALL,
        SYSTEM_ANAEROBIC: GAIN_MAINTENANCE,
    },
}

# Suggested sessions per week for the top three systems, given a limiter. The
# limiter gets the double session; endurance always keeps one long ride.
_EMPHASIS_BY_LIMITER: dict[str, list[tuple[str, int]]] = {
    LIMITER_THRESHOLD: [(SYSTEM_THRESHOLD, 2), (SYSTEM_VO2MAX, 1), (SYSTEM_ENDURANCE, 1)],
    LIMITER_VO2MAX: [(SYSTEM_VO2MAX, 2), (SYSTEM_THRESHOLD, 1), (SYSTEM_ENDURANCE, 1)],
    LIMITER_DURABILITY: [
        (SYSTEM_ENDURANCE, 2),
        (SYSTEM_THRESHOLD, 1),
        (SYSTEM_VO2MAX, 1),
    ],
}

# A balanced, limiter-agnostic week used when the model cannot back a limiter.
_FALLBACK_EMPHASIS = [
    (SYSTEM_VO2MAX, 1),
    (SYSTEM_THRESHOLD, 1),
    (SYSTEM_ENDURANCE, 1),
]


def _system_gain(system: str, gain: str, rationale: str) -> dict[str, Any]:
    return {"system": system, "gain": gain, "rationale": rationale}


def _emphasis(system: str, sessions: int) -> dict[str, Any]:
    return {"system": system, "label": _SYSTEM_LABEL[system], "sessions": sessions}


def _num(attr: dict | None) -> float | None:
    """The numeric estimate on an attribute, if it carries one."""
    if not attr:
        return None
    est = attr.get("estimate")
    return float(est) if isinstance(est, (int, float)) else None


def _score(attr: dict | None) -> str | None:
    if not attr:
        return None
    score = attr.get("score")
    return score if isinstance(score, str) and score != "unknown" else None


def _threshold_rationale(attrs: dict[str, dict]) -> tuple[str, str]:
    """(hypothesis, rationale) for a threshold-limited athlete."""
    ftp = _num(attrs.get("ftp"))
    mp = _num(attrs.get("map"))
    frac = _num(attrs.get("fractional_utilization"))
    aer = _score(attrs.get("aerobic_endurance"))

    hypothesis = (
        "Your aerobic ceiling is ahead of your sustainable power — threshold is the "
        "higher-return target right now, not more VO₂max work."
    )
    bits: list[str] = []
    if ftp and mp:
        bits.append(
            f"Your maximal aerobic power (~{mp:g} W) sits well above your sustainable "
            f"threshold (~{ftp:g} W)"
            + (f", only {frac:.0%} of that ceiling" if frac else "")
            + "."
        )
    if aer:
        bits.append(
            f"Your long rides indicate a {aer.replace('_', ' ')} aerobic base to build on."
        )
    bits.append(
        "The largest gap is between maximal aerobic power and sustainable threshold "
        "power, so threshold training is expected to produce greater gains than more "
        "VO₂max work."
    )
    return hypothesis, " ".join(bits)


def _vo2max_rationale(attrs: dict[str, dict]) -> tuple[str, str]:
    ftp = _num(attrs.get("ftp"))
    mp = _num(attrs.get("map"))
    frac = _num(attrs.get("fractional_utilization"))

    hypothesis = (
        "Your threshold already sits close to your aerobic ceiling — raising "
        "VO₂max/MAP offers more headroom than more threshold work."
    )
    bits: list[str] = []
    if ftp and mp:
        bits.append(
            f"Your threshold (~{ftp:g} W) is close to your maximal aerobic power "
            f"(~{mp:g} W)"
            + (f", about {frac:.0%} of it" if frac else "")
            + "."
        )
    bits.append(
        "With threshold this near the ceiling, further threshold work has little room; "
        "lifting the ceiling itself is the higher-return target."
    )
    return hypothesis, " ".join(bits)


def _durability_rationale(attrs: dict[str, dict]) -> tuple[str, str]:
    fat = _score(attrs.get("fatigue_resistance"))
    aer = _score(attrs.get("aerobic_endurance"))

    hypothesis = (
        "Your power fades late in long rides — extending durability is the "
        "higher-return target than more top-end work."
    )
    bits: list[str] = []
    if fat:
        bits.append(f"Fatigue resistance scores '{fat}' — power drops in the second half of long rides.")
    if aer:
        bits.append(f"Aerobic endurance scores '{aer}'.")
    bits.append(
        "Longer aerobic rides and endurance blocks are expected to pay off more than "
        "additional threshold or VO₂max intensity until durability catches up."
    )
    return hypothesis, " ".join(bits)


_RATIONALE_BY_LIMITER = {
    LIMITER_THRESHOLD: _threshold_rationale,
    LIMITER_VO2MAX: _vo2max_rationale,
    LIMITER_DURABILITY: _durability_rationale,
}

# Short per-system justification, keyed by (limiter, gain), kept generic so the
# machine-readable expected-gain map is self-explaining without the prose block.
_GAIN_REASON = {
    GAIN_LARGE: "the current limiter — highest expected return",
    GAIN_MODERATE: "supports the limiter and has room to improve",
    GAIN_SMALL: "already relatively developed — limited headroom",
    GAIN_MAINTENANCE: "maintain, not the priority right now",
}


def _fallback(reason: str) -> dict[str, Any]:
    """A neutral recommendation telling the caller to keep its own periodization."""
    gains = [
        _system_gain(SYSTEM_VO2MAX, GAIN_MODERATE, "balanced default"),
        _system_gain(SYSTEM_THRESHOLD, GAIN_MODERATE, "balanced default"),
        _system_gain(SYSTEM_ENDURANCE, GAIN_MAINTENANCE, "maintain aerobic base"),
    ]
    return {
        "sufficient": False,
        "limiter": None,
        "confidence": 0.0,
        "hypothesis": "",
        "rationale": reason,
        "expected_gain": gains,
        "weekly_emphasis": [_emphasis(s, n) for s, n in _FALLBACK_EMPHASIS],
    }


# The modalities a cycling stimulus can be delivered in. Gym is deliberately
# absent: every system here is an on-the-bike adaptation, and offering a
# strength session as a way to raise VO2max would be a scoring artefact rather
# than a recommendation.
_UTILITY_MODALITIES = ("road", "mtb", "gravel", "indoor")


def _utility_block(
    gain_map: dict[str, str],
    motivation: dict[str, Any] | None,
    upcoming_races: int,
) -> dict[str, Any] | None:
    """Rank (system, modality) options by expected athlete utility (#564).

    Returns ``None`` when there is no motivation model to rank with, which is
    what keeps this module's existing output byte-for-byte unchanged for every
    caller that has not opted in.
    """
    if not motivation:
        return None

    # Imported here rather than at module scope: training_utility reads this
    # module's gain and system constants, and a top-level import would close the
    # cycle.
    from services.training_utility import rank_options

    options = [
        (system, modality, gain)
        for system, gain in gain_map.items()
        for modality in _UTILITY_MODALITIES
    ]
    return rank_options(
        options, motivation=motivation, upcoming_races=upcoming_races
    )


def recommend_training_roi(
    attributes: dict[str, dict] | None,
    limiters: list[dict] | None,
    motivation: dict[str, Any] | None = None,
    upcoming_races: int = 0,
) -> dict[str, Any]:
    """Map the performance model + detected limiter to an ROI recommendation.

    ``attributes`` is the Athlete Performance Model attribute map and ``limiters``
    the ranked list from :func:`services.limiter_detection.detect_limiters`.

    Returns a dict with machine-readable ``expected_gain`` (per system: gain bucket
    + short reason), a ``weekly_emphasis`` list (system/label/sessions), a
    natural-language ``rationale`` and ``hypothesis`` citing the model, plus
    ``confidence`` and a ``sufficient`` flag. When the model has no confident
    limiter, ``sufficient`` is ``False`` and the caller should keep its existing
    periodization.

    ``motivation`` is the athlete's motivation model (#562). When supplied, the
    result carries an extra ``utility`` block ranking (system, modality) options
    by expected athlete utility (#564) — physiology remains the ``expected_gain``
    map above, unchanged, so a caller can always see how far the two diverge.
    Without it the output is exactly what it has always been.
    """
    attrs = attributes or {}
    limiter = top_limiter(limiters or [])
    if limiter is None or limiter not in _GAIN_BY_LIMITER:
        return _fallback(
            "The performance model has no confident limiter yet, so no ROI-based "
            "emphasis is asserted — fall back to standard periodization."
        )

    confidence = 0.0
    for cand in limiters or []:
        if cand.get("limiter") == limiter:
            raw = cand.get("confidence")
            confidence = float(raw) if isinstance(raw, (int, float)) else 0.0
            break

    gain_map = _GAIN_BY_LIMITER[limiter]
    expected_gain = [
        _system_gain(system, gain, _GAIN_REASON[gain])
        for system, gain in sorted(
            gain_map.items(), key=lambda kv: GAIN_RANK[kv[1]], reverse=True
        )
    ]
    weekly_emphasis = [_emphasis(s, n) for s, n in _EMPHASIS_BY_LIMITER[limiter]]
    hypothesis, rationale = _RATIONALE_BY_LIMITER[limiter](attrs)

    result = {
        "sufficient": True,
        "limiter": limiter,
        "confidence": round(confidence, 2),
        "hypothesis": hypothesis,
        "rationale": rationale,
        "expected_gain": expected_gain,
        "weekly_emphasis": weekly_emphasis,
    }
    utility = _utility_block(gain_map, motivation, upcoming_races)
    if utility is not None:
        result["utility"] = utility
    return result
