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

from services.activity_identity import SPORT_CYCLING, SPORT_RUNNING
from services.limiter_detection import (
    LIMITER_DURABILITY,
    LIMITER_RUN_CRITICAL_SPEED,
    LIMITER_RUN_DURABILITY,
    LIMITER_RUN_SPEED_CEILING,
    LIMITER_THRESHOLD,
    LIMITER_VO2MAX,
    pace_label,
    top_limiter,
    top_limiter_for_sport,
)

# Physiological systems a recommendation can emphasise.
SYSTEM_THRESHOLD = "threshold"
SYSTEM_VO2MAX = "vo2max"
SYSTEM_ENDURANCE = "endurance"
SYSTEM_ANAEROBIC = "anaerobic"

# The running systems (#718), named separately rather than reusing the four
# above. "Threshold" in this module has always meant watts at FTP; a running
# threshold session is a pace against Critical Speed, and letting one identifier
# mean both is the conflation #579, #712 and #716 each spent an issue undoing.
# Distinct names also mean a weekly emphasis can hold two threshold sessions —
# one on the bike and one on foot — without them collapsing into each other.
SYSTEM_RUN_THRESHOLD = "run_threshold"
SYSTEM_RUN_VO2MAX = "run_vo2max"
SYSTEM_RUN_ENDURANCE = "run_endurance"

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
    SYSTEM_RUN_THRESHOLD: "Threshold pace",
    SYSTEM_RUN_VO2MAX: "Run VO₂max",
    SYSTEM_RUN_ENDURANCE: "Long run",
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
    # Running (#718). Only running systems appear, which is the whole point: a
    # recommendation built from running evidence must not reach for a bike. There
    # is no running anaerobic system because nothing in the model measures one —
    # D′ is reported but we never know a short run effort was maximal, so a
    # recommendation resting on it would be a scoring artefact.
    LIMITER_RUN_CRITICAL_SPEED: {
        SYSTEM_RUN_THRESHOLD: GAIN_LARGE,
        SYSTEM_RUN_VO2MAX: GAIN_SMALL,
        SYSTEM_RUN_ENDURANCE: GAIN_MAINTENANCE,
    },
    LIMITER_RUN_SPEED_CEILING: {
        SYSTEM_RUN_VO2MAX: GAIN_LARGE,
        SYSTEM_RUN_THRESHOLD: GAIN_MODERATE,
        SYSTEM_RUN_ENDURANCE: GAIN_MAINTENANCE,
    },
    LIMITER_RUN_DURABILITY: {
        SYSTEM_RUN_ENDURANCE: GAIN_LARGE,
        SYSTEM_RUN_THRESHOLD: GAIN_MODERATE,
        SYSTEM_RUN_VO2MAX: GAIN_SMALL,
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
    # The running weeks are one session lighter than their cycling equivalents,
    # and that is the #717 ceiling showing up here rather than being left for the
    # plan gate to undo. Two hard running sessions plus a long run is a week most
    # runners can absorb; three is where the eccentric load starts outrunning the
    # tissue, and a recommendation that has to be capped downstream was the wrong
    # recommendation.
    LIMITER_RUN_CRITICAL_SPEED: [
        (SYSTEM_RUN_THRESHOLD, 2),
        (SYSTEM_RUN_ENDURANCE, 1),
    ],
    LIMITER_RUN_SPEED_CEILING: [
        (SYSTEM_RUN_VO2MAX, 1),
        (SYSTEM_RUN_THRESHOLD, 1),
        (SYSTEM_RUN_ENDURANCE, 1),
    ],
    LIMITER_RUN_DURABILITY: [
        (SYSTEM_RUN_ENDURANCE, 2),
        (SYSTEM_RUN_THRESHOLD, 1),
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


def _run_critical_speed_rationale(attrs: dict[str, dict]) -> tuple[str, str]:
    """(hypothesis, rationale) for a runner whose CS is behind their ceiling.

    Cites pace, not m/s. The stored attribute is a speed because the model does
    arithmetic with it; an athlete reads 4:35 /km and recognises it, and reads
    3.64 m/s and does not.
    """
    cs = _num(attrs.get("critical_speed"))
    ceiling = _num(attrs.get("velocity_at_vo2max"))
    frac = _num(attrs.get("run_fractional_utilization"))

    hypothesis = (
        "Your top-end running speed is ahead of the pace you can sustain — "
        "threshold-pace work is the higher-return target right now, not more "
        "track intervals."
    )
    bits: list[str] = []
    if cs and ceiling:
        bits.append(
            f"Your 5-minute speed ({pace_label(ceiling)}) sits well ahead of your "
            f"Critical Speed ({pace_label(cs)})"
            + (f", which is only {frac:.0%} of it" if frac else "")
            + "."
        )
    bits.append(
        "Tempo and threshold-pace running is expected to close that gap faster "
        "than more VO₂max intervals, which are also the highest-impact sessions "
        "in the sport."
    )
    return hypothesis, " ".join(bits)


def _run_speed_ceiling_rationale(attrs: dict[str, dict]) -> tuple[str, str]:
    cs = _num(attrs.get("critical_speed"))
    ceiling = _num(attrs.get("velocity_at_vo2max"))
    frac = _num(attrs.get("run_fractional_utilization"))

    hypothesis = (
        "Your sustainable pace already sits close to your top-end speed — raising "
        "the ceiling offers more headroom than more threshold-pace work."
    )
    bits: list[str] = []
    if cs and ceiling:
        bits.append(
            f"Your Critical Speed ({pace_label(cs)}) is close to your 5-minute speed "
            f"({pace_label(ceiling)})"
            + (f", about {frac:.0%} of it" if frac else "")
            + "."
        )
    bits.append(
        "With sustainable pace this near the ceiling, lifting the ceiling itself "
        "is the higher-return target — introduce it gradually, because these are "
        "the sessions running injuries come from."
    )
    return hypothesis, " ".join(bits)


def _run_durability_rationale(attrs: dict[str, dict]) -> tuple[str, str]:
    fat = _score(attrs.get("run_fatigue_resistance"))

    hypothesis = (
        "Your pace fades through long runs — extending durability is the "
        "higher-return target than more speed work."
    )
    bits: list[str] = []
    if fat:
        bits.append(
            f"Run fatigue resistance scores '{fat}' — pace drops in the second "
            "half of long runs."
        )
    bits.append(
        "Steady aerobic volume, built up gradually, is expected to pay off more "
        "than additional intensity until pace holds to the end of a long run."
    )
    return hypothesis, " ".join(bits)


_RATIONALE_BY_LIMITER = {
    LIMITER_THRESHOLD: _threshold_rationale,
    LIMITER_VO2MAX: _vo2max_rationale,
    LIMITER_DURABILITY: _durability_rationale,
    LIMITER_RUN_CRITICAL_SPEED: _run_critical_speed_rationale,
    LIMITER_RUN_SPEED_CEILING: _run_speed_ceiling_rationale,
    LIMITER_RUN_DURABILITY: _run_durability_rationale,
}

# Which sport each limiter's recommendation belongs to. Derived from the limiter
# rather than passed in, so a caller cannot ask for a cycling recommendation and
# be handed a running one.
_SPORT_BY_LIMITER = {
    LIMITER_THRESHOLD: SPORT_CYCLING,
    LIMITER_VO2MAX: SPORT_CYCLING,
    LIMITER_DURABILITY: SPORT_CYCLING,
    LIMITER_RUN_CRITICAL_SPEED: SPORT_RUNNING,
    LIMITER_RUN_SPEED_CEILING: SPORT_RUNNING,
    LIMITER_RUN_DURABILITY: SPORT_RUNNING,
}

# Short per-system justification, keyed by (limiter, gain), kept generic so the
# machine-readable expected-gain map is self-explaining without the prose block.
_GAIN_REASON = {
    GAIN_LARGE: "the current limiter — highest expected return",
    GAIN_MODERATE: "supports the limiter and has room to improve",
    GAIN_SMALL: "already relatively developed — limited headroom",
    GAIN_MAINTENANCE: "maintain, not the priority right now",
}


_RUN_FALLBACK_EMPHASIS = [
    (SYSTEM_RUN_THRESHOLD, 1),
    (SYSTEM_RUN_ENDURANCE, 1),
]


def _fallback(reason: str, sport: str = SPORT_CYCLING) -> dict[str, Any]:
    """A neutral recommendation telling the caller to keep its own periodization.

    Sport-scoped since #718, because the "neutral" week was not neutral: a
    runner with no confident limiter was handed a balanced *cycling* week —
    VO₂max, threshold and endurance on the bike — which is a cycling
    intervention on running evidence arriving through the one path nobody looks
    at, the one that fires when the model knows nothing.
    """
    if sport == SPORT_RUNNING:
        gains = [
            _system_gain(SYSTEM_RUN_THRESHOLD, GAIN_MODERATE, "balanced default"),
            _system_gain(
                SYSTEM_RUN_ENDURANCE, GAIN_MAINTENANCE, "maintain aerobic base"
            ),
        ]
        emphasis = _RUN_FALLBACK_EMPHASIS
    else:
        gains = [
            _system_gain(SYSTEM_VO2MAX, GAIN_MODERATE, "balanced default"),
            _system_gain(SYSTEM_THRESHOLD, GAIN_MODERATE, "balanced default"),
            _system_gain(SYSTEM_ENDURANCE, GAIN_MAINTENANCE, "maintain aerobic base"),
        ]
        emphasis = _FALLBACK_EMPHASIS
    return {
        "sufficient": False,
        "limiter": None,
        "sport": sport,
        "confidence": 0.0,
        "hypothesis": "",
        "rationale": reason,
        "expected_gain": gains,
        "weekly_emphasis": [_emphasis(s, n) for s, n in emphasis],
    }


# The modalities a cycling stimulus can be delivered in. Gym is deliberately
# absent: every system here is an on-the-bike adaptation, and offering a
# strength session as a way to raise VO2max would be a scoring artefact rather
# than a recommendation.
_UTILITY_MODALITIES = ("road", "mtb", "gravel", "indoor")

# Per sport (#718). Running has one modality, and listing the bike ones against
# a running system was the concrete form of "proposes a cycling intervention on
# running evidence": a runner whose limiter was durability would have been
# offered a gravel ride as a way to fix it, ranked and scored, with no hint that
# the evidence behind the ranking was about their legs on foot.
_UTILITY_MODALITIES_BY_SPORT = {
    SPORT_CYCLING: _UTILITY_MODALITIES,
    SPORT_RUNNING: ("run",),
}


def _utility_block(
    gain_map: dict[str, str],
    motivation: dict[str, Any] | None,
    upcoming_races: int,
    sport: str = SPORT_CYCLING,
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
        for modality in _UTILITY_MODALITIES_BY_SPORT.get(sport, _UTILITY_MODALITIES)
    ]
    return rank_options(
        options, motivation=motivation, upcoming_races=upcoming_races
    )


def recommend_training_roi(
    attributes: dict[str, dict] | None,
    limiters: list[dict] | None,
    motivation: dict[str, Any] | None = None,
    upcoming_races: int = 0,
    sport: str | None = None,
) -> dict[str, Any]:
    """Map the performance model + detected limiter to an ROI recommendation.

    ``attributes`` is the Athlete Performance Model attribute map and ``limiters``
    the ranked list from :func:`services.limiter_detection.detect_limiters`.

    ``sport`` picks which sport's limiter to build the recommendation from.
    ``None`` — the default, and what every pre-#718 caller gets — means
    "whichever sport the model is most sure about", which for a cycling-only
    athlete is cycling and reproduces the previous behaviour exactly. The result
    carries a ``sport`` field either way, so a caller never has to infer from the
    limiter id which sport it was handed.

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
    limiter = (
        top_limiter(limiters or [])
        if sport is None
        else top_limiter_for_sport(limiters, sport)
    )
    # Which sport this recommendation is actually about. Read off the limiter, not
    # off the argument, so the systems, the modalities and the fallback week can
    # never disagree with the evidence that produced them.
    for_sport = _SPORT_BY_LIMITER.get(limiter or "", sport or SPORT_CYCLING)
    if limiter is None or limiter not in _GAIN_BY_LIMITER:
        return _fallback(
            "The performance model has no confident limiter yet, so no ROI-based "
            "emphasis is asserted — fall back to standard periodization.",
            for_sport,
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
        "sport": for_sport,
        "confidence": round(confidence, 2),
        "hypothesis": hypothesis,
        "rationale": rationale,
        "expected_gain": expected_gain,
        "weekly_emphasis": weekly_emphasis,
    }
    utility = _utility_block(gain_map, motivation, upcoming_races, for_sport)
    if utility is not None:
        result["utility"] = utility
    return result


def recommend_training_roi_by_sport(
    attributes: dict[str, dict] | None,
    limiters: list[dict] | None,
    motivation: dict[str, Any] | None = None,
    upcoming_races: int = 0,
) -> dict[str, dict[str, Any]]:
    """One ROI recommendation per sport the athlete actually trains (#718).

    Keyed by sport, and **only** sports with a confident limiter appear — an
    athlete who rides and has never run gets a one-entry map, which is the honest
    answer rather than a running recommendation built from nothing.

    This is the entry point for a caller that has to serve a multisport athlete.
    :func:`recommend_training_roi` remains the single-sport answer and the one
    every existing caller uses; it is not a wrapper around this, because "the
    sport we are most sure about" and "every sport" are different questions and
    collapsing them would make the common case pay for the rare one.
    """
    from services.limiter_detection import top_limiter_by_sport

    return {
        sport: recommend_training_roi(
            attributes, limiters, motivation, upcoming_races, sport=sport
        )
        for sport in top_limiter_by_sport(limiters)
    }
