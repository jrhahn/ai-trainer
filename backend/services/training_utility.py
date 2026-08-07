"""Scoring training options by expected athlete utility, not return alone (#564).

:mod:`services.roi_recommendation` answers "which stimulus pays off most?" — a
question about physiology, and the only question the coach has been asking.
This module answers the one the athlete actually cares about: *given what this
athlete trains for, which option is worth doing?*

    utility = Σ wᵢ · scoreᵢ(option)

The weights are the athlete's own, from the motivation model (#562), learned
from what they say and do (#563). Nothing here hardcodes what matters; it
hardcodes only how to *measure* each axis once the athlete has told us how much
each one counts.

An option is a **(system, modality)** pair, and that pairing is the point. The
physiological prescription can be identical while the answer changes:

    threshold @ road   physiological 5.0   motivation 3.2   utility 4.0
    threshold @ mtb    physiological 4.0   motivation 8.5   utility 6.1

Same threshold work. Different bike. The second one is the one that happens.

Three properties this module is built around, all of them defensive:

* **Both sub-scores survive into the output.** A recommendation that cannot say
  how much of itself was physiology and how much was preference is not
  reviewable — by the athlete, by the coach explaining it (#565), or by whoever
  debugs it later.
* **Constraints filter, they do not discount.** "Avoid unnecessary crash risk"
  is not worth 0.1 utility; it removes options. But it can never empty the
  board — a filter that leaves nothing to recommend has malfunctioned.
* **A default motivation model must not change any recommendation.** An athlete
  nobody has learned anything about yet gets exactly the physiology-first
  ordering they get today. The regression guard is a test, not an intention.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from services import motivation_model as mm
from services.roi_recommendation import (
    GAIN_LARGE,
    GAIN_MAINTENANCE,
    GAIN_MODERATE,
    GAIN_SMALL,
    SYSTEM_ANAEROBIC,
    SYSTEM_ENDURANCE,
    SYSTEM_THRESHOLD,
    SYSTEM_VO2MAX,
)

# All scores live on 0–10, the scale the worked example in #561 is written in and
# a range a human can eyeball. Utility inherits it, because the weights sum to 1.
SCORE_MAX = 10.0

# The physiological axis, straight off the existing ROI gain buckets. This is the
# one component the motivation model does not get a vote on: it is a claim about
# the athlete's body, not their preferences.
_GAIN_SCORE = {
    GAIN_LARGE: 10.0,
    GAIN_MODERATE: 7.0,
    GAIN_SMALL: 4.0,
    GAIN_MAINTENANCE: 2.0,
}

# How reliably each modality actually gets done once it is on the calendar,
# before the athlete's own affinity is folded in. Indoor is weatherproof and
# short; a long off-road ride needs daylight, dry ground and a free morning.
_MODALITY_RELIABILITY = {
    mm.MODALITY_INDOOR: 0.95,
    mm.MODALITY_GYM: 0.8,
    mm.MODALITY_ROAD: 0.7,
    mm.MODALITY_GRAVEL: 0.6,
    mm.MODALITY_MTB: 0.55,
}

# Injury and crash exposure per modality, 0 = none. Technical off-road riding is
# genuinely more dangerous than a trainer, and an athlete who says "I can't
# afford a crash right now" means exactly this axis.
_MODALITY_RISK = {
    mm.MODALITY_INDOOR: 0.05,
    mm.MODALITY_GYM: 0.15,
    mm.MODALITY_ROAD: 0.45,
    mm.MODALITY_GRAVEL: 0.5,
    mm.MODALITY_MTB: 0.8,
}
# Above this, an option counts as high-risk and a risk-averse constraint removes
# it. Set so it catches MTB and leaves road riding alone: an athlete asking not
# to crash is not asking to stop cycling.
RISK_FILTER_THRESHOLD = 0.6

# How well each system transfers to race day. Threshold and VO₂max are what race
# results are made of; gym work supports them at one remove.
_SYSTEM_RACE_SPECIFICITY = {
    SYSTEM_THRESHOLD: 0.9,
    SYSTEM_VO2MAX: 0.85,
    SYSTEM_ANAEROBIC: 0.6,
    SYSTEM_ENDURANCE: 0.5,
}
# Structured work is easier to execute precisely indoors; a technical trail ride
# is a poor place to hold 300 W for 20 minutes.
_MODALITY_STRUCTURE = {
    mm.MODALITY_INDOOR: 1.0,
    mm.MODALITY_ROAD: 0.85,
    mm.MODALITY_GYM: 0.8,
    mm.MODALITY_GRAVEL: 0.6,
    mm.MODALITY_MTB: 0.4,
}

# Constraint texts that mean "keep me off the dangerous stuff". Matched loosely
# because the athlete writes these in their own words (#567), and bilingually
# because they write to their coach in whichever language they think in.
_RISK_AVERSE_PATTERNS = (
    re.compile(r"\bcrash\b", re.IGNORECASE),
    re.compile(r"\binjur\w*\b", re.IGNORECASE),
    re.compile(r"\bstay\s+healthy\b", re.IGNORECASE),
    re.compile(r"\bsturz\b", re.IGNORECASE),
    re.compile(r"\bverletz\w*\b", re.IGNORECASE),
    re.compile(r"\bgesund\s+bleiben\b", re.IGNORECASE),
)

_SPORT_TO_MODALITY = {
    "ride": mm.MODALITY_ROAD,
    "roadride": mm.MODALITY_ROAD,
    "virtualride": mm.MODALITY_INDOOR,
    "indoorride": mm.MODALITY_INDOOR,
    "mountainbikeride": mm.MODALITY_MTB,
    "emountainbikeride": mm.MODALITY_MTB,
    "mtb": mm.MODALITY_MTB,
    "gravelride": mm.MODALITY_GRAVEL,
    "gravel": mm.MODALITY_GRAVEL,
    "cyclocross": mm.MODALITY_GRAVEL,
    "weighttraining": mm.MODALITY_GYM,
    "workout": mm.MODALITY_GYM,
    "strength": mm.MODALITY_GYM,
}


def modality_for_sport(sport_type: str | None) -> str | None:
    """Map a provider sport type onto a motivation modality, or ``None``.

    Shared with the behavioural inference pass so a swap the athlete sees on
    their activity feed is scored as the same modality the planner reasons about.
    """
    key = (sport_type or "").strip().casefold().replace(" ", "").replace("_", "")
    if not key:
        return None
    return _SPORT_TO_MODALITY.get(key)


def _scale(value: float) -> float:
    return round(max(0.0, min(1.0, value)) * SCORE_MAX, 2)


def is_risk_averse(constraints: Iterable[Mapping[str, Any]] | None) -> bool:
    """Whether the athlete's constraints ask to avoid crash and injury exposure."""
    for entry in constraints or []:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("status") == mm.STATUS_RETIRED:
            continue
        text = str(entry.get("text") or "")
        if any(pattern.search(text) for pattern in _RISK_AVERSE_PATTERNS):
            return True
    return False


def score_components(
    system: str,
    modality: str,
    *,
    gain: str,
    affinity: Mapping[str, float],
    upcoming_races: int = 0,
) -> dict[str, float]:
    """Score one option on every motivation axis, each 0–10.

    Pure: the same option and the same athlete always produce the same numbers,
    which is what lets a recommendation be re-derived when someone asks why.
    """
    like = float(affinity.get(modality, mm.NEUTRAL_AFFINITY))
    reliability = _MODALITY_RELIABILITY.get(modality, 0.7)
    risk = _MODALITY_RISK.get(modality, 0.4)

    return {
        # What the athlete's body gets out of it — the existing ROI, untouched.
        "adaptation": _GAIN_SCORE.get(gain, 4.0),
        # Whether they want to do it at all.
        "enjoyment": _scale(like),
        # Whether it will actually happen: an athlete shows up for the rides they
        # like, and for the ones that are hard to cancel.
        "consistency": _scale(0.5 * reliability + 0.5 * like),
        # What it costs them in exposure.
        "health": _scale(1.0 - risk),
        # Whether it makes them faster on a day that counts — and only if such a
        # day exists. Race specificity is worth nothing with no race on the
        # calendar, which is what stops a road bias from surviving an athlete
        # who has stopped racing.
        "race_performance": (
            _scale(
                _SYSTEM_RACE_SPECIFICITY.get(system, 0.5)
                * _MODALITY_STRUCTURE.get(modality, 0.7)
            )
            if upcoming_races
            else 0.0
        ),
    }


def score_option(
    system: str,
    modality: str,
    *,
    gain: str,
    motivation: Mapping[str, Any],
    upcoming_races: int = 0,
) -> dict[str, Any]:
    """One scored option, carrying everything needed to explain itself.

    ``physiological_score`` is the adaptation axis alone — the recommendation the
    coach would have made before any of this. ``motivation_score`` is the
    weighted average of the remaining axes, renormalized over their own weights
    so it stays on 0–10 and stays readable next to the physiological one.
    ``utility`` is the full weighted sum, which is what actually ranks options.
    """
    weights = mm.normalize_weights(
        motivation.get("utility_weights"), pinned=motivation.get("pinned_weights")
    )
    affinity = mm.normalize_modality_affinity(motivation.get("modality_affinity"))
    components = score_components(
        system,
        modality,
        gain=gain,
        affinity=affinity,
        upcoming_races=upcoming_races,
    )

    utility = sum(weights[c] * components[c] for c in mm.MOTIVATION_COMPONENTS)

    motivation_mass = sum(
        weights[c] for c in mm.MOTIVATION_COMPONENTS if c != "adaptation"
    )
    motivation_score = (
        sum(
            weights[c] * components[c]
            for c in mm.MOTIVATION_COMPONENTS
            if c != "adaptation"
        )
        / motivation_mass
        if motivation_mass > 0
        else 0.0
    )

    return {
        "system": system,
        "modality": modality,
        "gain": gain,
        "physiological_score": round(components["adaptation"], 2),
        "motivation_score": round(motivation_score, 2),
        "utility": round(utility, 2),
        "components": {k: round(v, 2) for k, v in components.items()},
    }


def rank_options(
    options: Sequence[tuple[str, str, str]],
    *,
    motivation: Mapping[str, Any] | None,
    upcoming_races: int = 0,
) -> dict[str, Any]:
    """Rank ``(system, modality, gain)`` options by expected athlete utility.

    Returns the ranked options, the ones a constraint removed, and the exact
    weights the ranking used — the last of those because a recommendation whose
    weights are not recorded cannot be audited when it changes next month.

    Constraints are applied as a filter, never as a discount, and never to the
    point of leaving nothing: if every option is high-risk, the filter is
    reported as inapplicable rather than silently returning no advice.
    """
    model = mm.normalize_model(motivation) if motivation else mm.default_model()
    weights = mm.normalize_weights(
        model.get("utility_weights"), pinned=model.get("pinned_weights")
    )

    scored = [
        score_option(
            system,
            modality,
            gain=gain,
            motivation=model,
            upcoming_races=upcoming_races,
        )
        for system, modality, gain in options
    ]

    excluded: list[dict[str, Any]] = []
    if is_risk_averse(model.get("constraints")):
        risky = [
            option
            for option in scored
            if _MODALITY_RISK.get(option["modality"], 0.4) > RISK_FILTER_THRESHOLD
        ]
        # A filter that empties the board has malfunctioned; better to recommend
        # a risky option and say so than to recommend nothing.
        if risky and len(risky) < len(scored):
            excluded = [
                {**option, "excluded_by": "constraint: crash and injury risk"}
                for option in risky
            ]
            keep = {id(option) for option in risky}
            scored = [option for option in scored if id(option) not in keep]

    scored.sort(
        key=lambda o: (o["utility"], o["physiological_score"]),
        reverse=True,
    )
    return {
        "options": scored,
        "excluded": excluded,
        "weights": weights,
        "modality_affinity": mm.normalize_modality_affinity(
            model.get("modality_affinity")
        ),
    }
