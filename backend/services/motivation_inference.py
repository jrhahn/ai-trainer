"""Learning what the athlete is training for, from what they say and do (#563).

#562 gave motivation a home. This fills it, from the two places the answer
actually shows up:

* **What the athlete says.** "I don't care about races", "I want more trail
  time", "I don't chase FTP". :func:`capture_motivation_from_message` runs on
  every coach turn, deterministically — the same idiom as the weather-preference
  and home-location captures (#495), and for the same reasons: it costs no
  tokens, it is testable, and every update carries the athlete's own words as
  its ``source_snippet`` so the athlete can see why the coach believes it (#567).
* **What the athlete does.** Which modality they actually ride when the plan
  said something else, whether they follow structure or improvise, whether they
  put races on the calendar. :func:`refresh_motivation_from_behaviour` runs as a
  continuous-learning step and reads it off the stored rides.

The hard part is not extraction, it is **restraint**. An objective is the thing
every planning decision gets scored against (#564), so a single sentence must
not be able to redefine it. Three rules enforce that, and all three live in
:mod:`services.motivation_model` rather than here, so a future third source of
evidence inherits them:

1. A statement enters as a *secondary* objective capped at
   ``INITIAL_CONFIDENCE_CAP``, and only recurrence promotes it to primary.
2. Behaviour moves a utility weight by at most ``MAX_WEIGHT_NUDGE`` per run, so
   it takes weeks of a consistent signal to change what the athlete values.
3. Evidence that argues against a stored objective marks it ``contradicted``
   for the athlete to settle, rather than flipping it.

And underneath all of it, the ``user_set`` override from #562: none of this can
overwrite an objective the athlete stated by hand.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import motivation_model as mm
from services.dates import app_today_iso
from services.training_utility import modality_for_sport

logger = logging.getLogger(__name__)

SNIPPET_MAX_LEN = 200

# How much history the behavioural pass reads. Long enough that a holiday week or
# one bad month does not define the athlete, short enough that a genuine change
# of direction is visible within a season.
BEHAVIOUR_RIDE_LIMIT = 60
BEHAVIOUR_WINDOW_DAYS = 84
# Below this there is no pattern, only rides.
BEHAVIOUR_MIN_RIDES = 8

# A stated objective is strong evidence — stronger than a correlation over a
# handful of rides — but the accrual curve still caps a first sighting, so this
# is what a *repeated* statement converges towards.
STATED_CONFIDENCE = 0.7

# Sport types that mean "off the tarmac". Used to tell a deliberate modality
# choice from the prescribed one.
_OFF_ROAD_SPORTS = {
    "mountainbikeride",
    "mtb",
    "gravelride",
    "gravel",
    "emountainbikeride",
    "cyclocross",
}


# ---------------------------------------------------------------------------
# What the athlete says
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MotivationSignal:
    """One piece of motivation evidence read out of a message.

    ``objective`` is the athlete-facing statement to store; ``weight_nudges`` is
    how this evidence argues the utility vector should shift. A signal may carry
    either or both — "I don't chase FTP" says something about weights without
    naming an objective.
    """

    weight_nudges: dict[str, float] = field(default_factory=dict)
    modality_nudges: dict[str, float] = field(default_factory=dict)
    objective: str | None = None
    constraint: str | None = None


# (pattern, signal factory). Deterministic and bilingual, matching the
# weather-preference extractor: the athlete writes to their coach in whichever
# language they think in.
_STATEMENT_PATTERNS: tuple[tuple[str, MotivationSignal], ...] = (
    # --- racing does not matter -------------------------------------------
    (
        r"\b(?:i\s+)?(?:don't|do\s+not|dont)\s+(?:really\s+)?care\s+about\s+(?:the\s+)?rac(?:es|ing)\b",
        MotivationSignal(weight_nudges={"race_performance": -0.05, "enjoyment": 0.02}),
    ),
    (
        r"\b(?:i'?m\s+)?not\s+(?:really\s+)?(?:interested\s+in|into)\s+rac(?:es|ing)\b",
        MotivationSignal(weight_nudges={"race_performance": -0.05, "enjoyment": 0.02}),
    ),
    (
        r"\bich\s+(?:will|m(?:ö|oe)chte)\s+nicht\s+(?:mehr\s+)?(?:rennen\s+fahren|racen)\b",
        MotivationSignal(weight_nudges={"race_performance": -0.05, "enjoyment": 0.02}),
    ),
    (
        r"\brennen\s+(?:sind|interessieren)\s+mich\s+nicht\b",
        MotivationSignal(weight_nudges={"race_performance": -0.05, "enjoyment": 0.02}),
    ),
    # --- racing does matter -----------------------------------------------
    (
        r"\b(?:i\s+)?(?:want|need)\s+to\s+(?:win|podium|place\s+well)\b",
        MotivationSignal(
            weight_nudges={"race_performance": 0.05},
            objective="Perform at races",
        ),
    ),
    (
        r"\bmy\s+(?:a[-\s]?race|goal\s+race|target\s+race)\b",
        MotivationSignal(
            weight_nudges={"race_performance": 0.04},
            objective="Perform at races",
        ),
    ),
    # --- riding for its own sake ------------------------------------------
    (
        r"\b(?:i\s+)?(?:train|ride)\s+(?:to|for)\s+(?:just\s+)?(?:enjoy|have\s+fun|feel\s+good)\b",
        MotivationSignal(
            weight_nudges={"enjoyment": 0.05, "race_performance": -0.02},
            objective="Enjoy riding — fitness serves the riding, not the other way round",
        ),
    ),
    (
        r"\b(?:i\s+)?(?:just\s+)?(?:want\s+to\s+)?(?:enjoy|have\s+fun\s+on)\s+(?:my\s+)?rid(?:es|ing)\b",
        MotivationSignal(
            weight_nudges={"enjoyment": 0.05},
            objective="Enjoy riding — fitness serves the riding, not the other way round",
        ),
    ),
    (
        r"\bich\s+fahre\s+(?:zum\s+)?spa(?:ß|ss)\b",
        MotivationSignal(
            weight_nudges={"enjoyment": 0.05},
            objective="Enjoy riding — fitness serves the riding, not the other way round",
        ),
    ),
    # --- trails ------------------------------------------------------------
    (
        r"\b(?:i\s+)?want\s+(?:more|extra)\s+(?:trail|singletrack)\s+(?:time|riding)\b",
        MotivationSignal(
            weight_nudges={"enjoyment": 0.05},
            modality_nudges={"mtb": 0.04},
            objective="Maximize time on technical trails",
        ),
    ),
    (
        r"\b(?:i\s+)?want\s+to\s+ride\s+more\s+(?:trails|singletrack|technical\s+\w+)\b",
        MotivationSignal(
            weight_nudges={"enjoyment": 0.05},
            modality_nudges={"mtb": 0.04},
            objective="Maximize time on technical trails",
        ),
    ),
    (
        r"\bich\s+(?:will|m(?:ö|oe)chte)\s+mehr\s+(?:trails|trail\s*zeit|singletrail)\b",
        MotivationSignal(
            weight_nudges={"enjoyment": 0.05},
            modality_nudges={"mtb": 0.04},
            objective="Maximize time on technical trails",
        ),
    ),
    (
        r"\b(?:i'?d\s+|i\s+would\s+)?rather\s+ride\s+(?:the\s+)?(?:mtb|mountain\s*bike|trails)\b",
        MotivationSignal(
            weight_nudges={"enjoyment": 0.04},
            modality_nudges={"mtb": 0.05, "road": -0.03},
        ),
    ),
    # --- not chasing numbers ----------------------------------------------
    (
        r"\b(?:i\s+)?(?:don't|do\s+not|dont)\s+(?:chase|care\s+about)\s+(?:my\s+)?(?:ftp|watts|numbers|power\s+numbers)\b",
        MotivationSignal(weight_nudges={"adaptation": -0.05, "enjoyment": 0.02}),
    ),
    (
        r"\b(?:ftp|watts|numbers)\s+(?:isn't|is\s+not|aren't|are\s+not)\s+(?:the\s+point|important\s+to\s+me|what\s+i'?m\s+after)\b",
        MotivationSignal(weight_nudges={"adaptation": -0.05}),
    ),
    (
        r"\bich\s+jage\s+keine\s+(?:watt|zahlen|ftp)\b",
        MotivationSignal(weight_nudges={"adaptation": -0.05}),
    ),
    # --- health & longevity -------------------------------------------------
    (
        r"\b(?:i\s+)?(?:want|need)\s+to\s+(?:stay|keep|remain)\s+healthy\b",
        MotivationSignal(
            weight_nudges={"health": 0.05},
            constraint="Stay healthy",
        ),
    ),
    (
        r"\bich\s+will\s+gesund\s+bleiben\b",
        MotivationSignal(weight_nudges={"health": 0.05}, constraint="Stay healthy"),
    ),
    (
        r"\b(?:i\s+)?(?:can't|cannot|don't\s+want\s+to)\s+(?:risk|afford)\s+(?:an?\s+)?(?:injur|crash)\w*\b",
        MotivationSignal(
            weight_nudges={"health": 0.04},
            constraint="Avoid unnecessary crash and injury risk",
        ),
    ),
    # --- consistency --------------------------------------------------------
    (
        r"\b(?:i\s+)?(?:just\s+)?want\s+to\s+(?:be|stay)\s+consistent\b",
        MotivationSignal(weight_nudges={"consistency": 0.05}),
    ),
    (
        r"\b(?:i\s+)?(?:want|need)\s+to\s+ride\s+(?:more\s+)?regularly\b",
        MotivationSignal(weight_nudges={"consistency": 0.05}),
    ),
)

_COMPILED_PATTERNS = tuple(
    (re.compile(pattern, re.IGNORECASE), signal)
    for pattern, signal in _STATEMENT_PATTERNS
)

# "my goal is to ride the Trans-Alp", "I train so I can keep up on the club ride".
# A free-form objective the fixed patterns above cannot enumerate.
_FREE_OBJECTIVE_PATTERNS = (
    re.compile(
        r"\bmy\s+(?:real\s+)?goal\s+is\s+to\s+(?P<objective>[^.!?\n]{6,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+i\s+(?:really\s+)?want\s+is\s+to\s+(?P<objective>[^.!?\n]{6,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s+train\s+so\s+(?:that\s+)?i\s+can\s+(?P<objective>[^.!?\n]{6,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmein\s+ziel\s+ist\s+(?:es,?\s+)?(?P<objective>[^.!?\n]{6,120})",
        re.IGNORECASE,
    ),
)


def extract_motivation_statements(text: str) -> list[MotivationSignal]:
    """Read motivation evidence out of one message.

    Pure and side-effect free so the patterns can be tested directly. Returns one
    signal per distinct match; an empty list is the common case and costs a few
    regex scans.
    """
    if not text or not text.strip():
        return []

    signals: list[MotivationSignal] = []
    seen: set[tuple] = set()

    for pattern, signal in _COMPILED_PATTERNS:
        if not pattern.search(text):
            continue
        key = (
            signal.objective,
            signal.constraint,
            tuple(sorted(signal.weight_nudges.items())),
            tuple(sorted(signal.modality_nudges.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        signals.append(
            MotivationSignal(
                weight_nudges=dict(signal.weight_nudges),
                modality_nudges=dict(signal.modality_nudges),
                objective=signal.objective,
                constraint=signal.constraint,
            )
        )

    for pattern in _FREE_OBJECTIVE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        stated = " ".join(match.group("objective").split()).strip(" ,;:")
        if not stated:
            continue
        objective = stated[0].upper() + stated[1:]
        if objective.casefold() in {s.objective.casefold() for s in signals if s.objective}:
            continue
        signals.append(MotivationSignal(objective=objective))

    return signals


def _quote(message: str) -> str:
    return " ".join((message or "").split())[:SNIPPET_MAX_LEN]


async def capture_motivation_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
    *,
    now: datetime | None = None,
) -> models.AthleteMotivationModel | None:
    """Fold what the athlete just said into their motivation model (#563).

    Best-effort by construction: the caller runs this beside the other
    per-message captures on the chat path, and a failure here must never cost the
    athlete their reply.

    Every update carries the athlete's own sentence as its ``source_snippet``,
    which is what makes an inferred objective correctable rather than mysterious
    (#567). Nothing here can overwrite a ``user_set`` objective — that guard is
    in the write gate, not in this function's good behaviour.
    """
    signals = extract_motivation_statements(message)
    if not signals:
        return None

    snippet = _quote(message)
    objectives: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    nudges: dict[str, float] = {}
    modality_nudges: dict[str, float] = {}

    for signal in signals:
        if signal.objective:
            objectives.append(
                {
                    "text": signal.objective,
                    "confidence": STATED_CONFIDENCE,
                    "source_snippet": snippet,
                }
            )
        if signal.constraint:
            constraints.append(
                {
                    "text": signal.constraint,
                    "confidence": STATED_CONFIDENCE,
                    "source_snippet": snippet,
                }
            )
        for component, delta in signal.weight_nudges.items():
            nudges[component] = nudges.get(component, 0.0) + delta
        for modality, delta in signal.modality_nudges.items():
            modality_nudges[modality] = modality_nudges.get(modality, 0.0) + delta

    existing = await crud.get_athlete_motivation_model(db, user_id)
    current = crud.motivation_model_as_dict(existing)

    updates: dict[str, Any] = {}
    if objectives:
        updates["secondary_objectives"] = current["secondary_objectives"] + objectives
    if constraints:
        updates["constraints"] = current["constraints"] + constraints
    if nudges:
        updates["utility_weights"] = mm.apply_weight_evidence(
            current["utility_weights"],
            nudges,
            pinned=current["pinned_weights"],
        )
    if modality_nudges:
        updates["modality_affinity"] = mm.apply_modality_evidence(
            current["modality_affinity"], modality_nudges
        )

    if not updates:
        return None

    return await crud.upsert_athlete_motivation_model(
        db,
        user_id,
        updates=updates,
        source=mm.SOURCE_INFERRED,
        accrue=True,
        promote=True,
        now=now,
    )


# ---------------------------------------------------------------------------
# What the athlete does
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BehaviourEvidence:
    """The behavioural summary one inference run scored, kept for the audit line."""

    rides: int = 0
    off_plan_rides: int = 0
    modality_swaps: int = 0
    matched_rides: int = 0
    upcoming_races: int = 0
    # How many of the rides landed in each modality. Revealed preference: the
    # one currency an athlete cannot talk up (#564).
    modality_counts: dict[str, int] = field(default_factory=dict)

    @property
    def has_signal(self) -> bool:
        return self.rides >= BEHAVIOUR_MIN_RIDES


def _sport(value: str | None) -> str:
    return (value or "").strip().casefold().replace(" ", "").replace("_", "")


def _planned_sport(ride: models.RideMetric) -> str:
    snapshot = ride.matched_plan_snapshot
    if not isinstance(snapshot, dict):
        return ""
    for key in ("sportType", "sport_type", "workoutType", "workout_type"):
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return _sport(value)
    return ""


def summarize_behaviour(
    rides: list[models.RideMetric],
    *,
    upcoming_races: int = 0,
) -> BehaviourEvidence:
    """Reduce recent rides to the handful of counts the weights are argued from.

    Pure, so the scoring rule can be tested without a database.
    """
    evidence = BehaviourEvidence(rides=len(rides), upcoming_races=upcoming_races)
    for ride in rides:
        planned = _planned_sport(ride)
        actual = _sport(ride.sport_type)
        modality = modality_for_sport(ride.sport_type)
        if modality is not None:
            evidence.modality_counts[modality] = (
                evidence.modality_counts.get(modality, 0) + 1
            )
        if ride.plan_match_status == "matched" and planned:
            evidence.matched_rides += 1
            # Rode, but rode something else: the plan said road, the athlete took
            # the MTB. That is a preference expressed in the only currency that
            # cannot be talked up — time actually spent.
            if actual and actual != planned:
                if actual in _OFF_ROAD_SPORTS or planned in _OFF_ROAD_SPORTS:
                    evidence.modality_swaps += 1
        elif ride.plan_match_status != "matched":
            evidence.off_plan_rides += 1
    return evidence


def score_behaviour(evidence: BehaviourEvidence) -> dict[str, float]:
    """Turn the behavioural summary into weight nudges.

    Deliberately small numbers. Each is bounded again by
    :func:`motivation_model.apply_weight_evidence`, so what this returns is an
    *argument* about direction, and the gate decides how far one run may act on
    it. Several weeks of the same argument is what actually moves the vector.
    """
    if not evidence.has_signal:
        return {}

    nudges: dict[str, float] = {}
    total = float(evidence.rides)

    swap_rate = evidence.modality_swaps / total
    off_plan_rate = evidence.off_plan_rides / total
    adherence = evidence.matched_rides / total

    # Choosing a different discipline than the one prescribed, repeatedly, is the
    # clearest behavioural statement that the ride itself is the point.
    if swap_rate >= 0.2:
        nudges["enjoyment"] = 0.03 * min(1.0, swap_rate / 0.4)
        nudges["adaptation"] = -0.02 * min(1.0, swap_rate / 0.4)

    # Riding, but not what was planned, says the same thing more weakly — it can
    # equally be a busy month, so it argues less far.
    if off_plan_rate >= 0.4:
        nudges["enjoyment"] = nudges.get("enjoyment", 0.0) + 0.02
        nudges["consistency"] = nudges.get("consistency", 0.0) - 0.01

    # An athlete who does the session that was written down values the structure.
    if adherence >= 0.6:
        nudges["consistency"] = nudges.get("consistency", 0.0) + 0.02
        nudges["adaptation"] = nudges.get("adaptation", 0.0) + 0.02

    # Putting a race on the calendar is a commitment, not a mood.
    if evidence.upcoming_races:
        nudges["race_performance"] = 0.02 * min(2, evidence.upcoming_races)

    return nudges


# The modalities ride data can speak to. Gym affinity is not observable from a
# cycling feed, so the behavioural pass leaves it alone rather than inferring a
# dislike from silence.
_RIDEABLE_MODALITIES = (
    mm.MODALITY_ROAD,
    mm.MODALITY_MTB,
    mm.MODALITY_GRAVEL,
    mm.MODALITY_INDOOR,
)
_EXPECTED_SHARE = 1.0 / len(_RIDEABLE_MODALITIES)


def score_modality_affinity(evidence: BehaviourEvidence) -> dict[str, float]:
    """Turn what the athlete actually rode into affinity nudges (#564).

    Revealed preference, mapped linearly from observed share: a modality the
    athlete rides more than an even split argues up, one they never touch argues
    down, and an even spread argues nothing. Bounded again by
    :func:`motivation_model.apply_modality_evidence`, so a single block of MTB
    weeks cannot declare the athlete a mountain biker.
    """
    if not evidence.has_signal:
        return {}

    ridden = sum(evidence.modality_counts.get(m, 0) for m in _RIDEABLE_MODALITIES)
    if not ridden:
        return {}

    nudges: dict[str, float] = {}
    for modality in _RIDEABLE_MODALITIES:
        share = evidence.modality_counts.get(modality, 0) / ridden
        direction = (share - _EXPECTED_SHARE) / _EXPECTED_SHARE
        nudges[modality] = mm.MAX_AFFINITY_NUDGE * max(-1.0, min(1.0, direction))
    return nudges


def detect_behaviour_contradictions(
    model: dict[str, Any],
    evidence: BehaviourEvidence,
) -> list[dict[str, Any]]:
    """Objectives the athlete's behaviour argues against.

    Returns the entries re-marked as ``contradicted``, for the athlete to settle.
    The coach does not get to overrule what the athlete told it — it gets to
    notice the tension and say so (#567).
    """
    if not evidence.has_signal:
        return []

    flagged: list[dict[str, Any]] = []
    for entry in model.get("secondary_objectives") or []:
        text = entry.get("text", "").casefold()
        races_matter = "race" in text or "podium" in text or "win" in text
        if races_matter and evidence.upcoming_races == 0 and entry["status"] == mm.STATUS_ACTIVE:
            flagged.append(
                mm.contradict_entry(
                    entry,
                    note=(
                        "You told me racing matters, but there is no race on your "
                        "calendar. Still the goal, or has that changed?"
                    ),
                )
            )
    return flagged


async def refresh_motivation_from_behaviour(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Update the motivation model from recent training behaviour (#563).

    A continuous-learning step with the pipeline's ``-> int`` contract: returns 1
    when the model changed, 0 otherwise. Reads the same rides the rest of the
    coach reasons about, so a swap the athlete can see on their activity feed is
    the same swap that moved their weights.
    """
    moment = now or datetime.now(timezone.utc)
    rides = await crud.get_ride_metrics_history(
        db, user.id, limit=BEHAVIOUR_RIDE_LIMIT
    )
    cutoff = (moment - timedelta(days=BEHAVIOUR_WINDOW_DAYS)).date().isoformat()
    recent = [ride for ride in rides if (ride.activity_date or "") >= cutoff]

    today = app_today_iso(timezone_name=timezone_name)
    races = await crud.get_race_events(db, user.id)
    upcoming = sum(1 for race in races if (race.date or "") >= today)

    evidence = summarize_behaviour(recent, upcoming_races=upcoming)
    if not evidence.has_signal:
        return 0

    current = crud.motivation_model_as_dict(
        await crud.get_athlete_motivation_model(db, user.id)
    )
    nudges = score_behaviour(evidence)
    affinity_nudges = score_modality_affinity(evidence)
    contradicted = detect_behaviour_contradictions(current, evidence)

    updates: dict[str, Any] = {}
    if affinity_nudges:
        affinity = mm.apply_modality_evidence(
            current["modality_affinity"], affinity_nudges
        )
        if affinity != current["modality_affinity"]:
            updates["modality_affinity"] = affinity
    if nudges:
        moved = mm.apply_weight_evidence(
            current["utility_weights"], nudges, pinned=current["pinned_weights"]
        )
        if moved != current["utility_weights"]:
            updates["utility_weights"] = moved
    if contradicted:
        flagged_keys = {entry["text"].casefold() for entry in contradicted}
        updates["secondary_objectives"] = contradicted + [
            entry
            for entry in current["secondary_objectives"]
            if entry["text"].casefold() not in flagged_keys
        ]

    if not updates:
        return 0

    # A contradiction is a correction, not a re-observation, so this write does
    # not accrue: flagging an objective must not also strengthen it.
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates=updates,
        source=mm.SOURCE_INFERRED,
        accrue=False,
        promote=True,
        now=moment,
    )
    logger.info(
        "motivation.behaviour user_id=%s rides=%d swaps=%d off_plan=%d races=%d "
        "nudges=%s affinity=%s contradicted=%d",
        user.id,
        evidence.rides,
        evidence.modality_swaps,
        evidence.off_plan_rides,
        evidence.upcoming_races,
        {k: round(v, 4) for k, v in nudges.items()},
        {k: round(v, 4) for k, v in affinity_nudges.items()},
        len(contradicted),
    )
    return 1
