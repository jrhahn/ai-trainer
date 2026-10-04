"""Concurrent-training interference, decided from the plan alone (#715).

A cyclist who lifts is not doing two independent training programmes. Hickson's
interference effect and the Wilson et al. (2012) meta-analysis establish three
things about combining them, none of which this app knew:

**It is directional.** Endurance work impairs strength and power adaptation more
than strength work impairs endurance. So "which came first" is not a detail —
it changes which adaptation the athlete loses.

**It is modality-dependent.** Running interferes more than cycling, via the
eccentric loading cycling does not have. The same hour costs a lifter more when
it is run than when it is ridden.

**It is sensitive to ordering and spacing.** Within a day, the quality session
should come first and the two should be separated; across days, heavy lower-body
work the day before a key session arrives on legs that have to produce the
session's power.

Until #715 the plan gate could not act on any of it, and said so: the module
docstring in ``services/plan_coherence`` drew the line at "an identical session
twice, or strength twice, is decidable from the plan alone — whether a strength
day may sit next to a threshold session depends on whether it loads the legs,
which is real coaching, stated in the prompt and left there."

#714 moved that line. A planned gym session now carries its prescribed lifts
(``PlanDay.strength_exercises``), so *whether it loads the legs* is no longer a
judgement — it is a list of exercises with an intensity unit attached, and the
answer is readable from the plan. That is what makes these rules safe to enforce
at the gate rather than merely mention, and it is the only reason this module can
exist.

What is still judgement stays out. A prescription with no intensity unit is not
read as heavy; a session with no prescription at all is not guessed at from its
description. Both are reported to the coach and left alone, because a guard that
reverts a write on an inference would cost the athlete sessions it had no
business touching — the #651 mistake, which every guard here is shaped to avoid.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from schemas import day_slot, day_sport
from services.activity_identity import SPORT_CYCLING, SPORT_RUNNING, SPORT_STRENGTH
from services.plan_coherence import DEFAULT_HORIZON_DAYS

# The rule identifiers. Stable strings: they key the before/after comparison the
# pipeline gate makes, and they name the finding in the coach's prompt.
RULE_HEAVY_LEGS_BEFORE_KEY = "heavy-legs-before-key"
RULE_STRENGTH_BEFORE_INTENSITY = "strength-before-intensity"
RULE_INSUFFICIENT_SEPARATION = "insufficient-separation"

# Endurance sessions whose *quality* is the point of the session, and which
# therefore have to be ridden on legs that can produce it. Deliberately not
# "anything that is not rest": an endurance ride the day after heavy squats is
# uncomfortable, a 4×8 min threshold session the day after heavy squats is a
# different session than the one that was prescribed.
#
# ``tempo`` is left out on purpose. It sits in the band where the session still
# does its job on tired legs, and including it would make the guard fire on a
# combination a competent coach writes deliberately — lifting Tuesday, tempo
# Wednesday is a normal week, not an error.
KEY_WORKOUT_TYPES = frozenset({"intervals", "race"})

# Endurance sports. A strength session is not "the other sport" to another
# strength session, and two gym days in a row are already covered by
# ``plan_coherence.find_stacked_strength``.
_ENDURANCE_SPORTS = frozenset({SPORT_CYCLING, SPORT_RUNNING})

# Compound lower-body lifts: the ones that leave the legs unable to produce
# power the next day. Matched as substrings of the normalised exercise name, so
# "barbell back squat" and "squat" are both caught.
#
# Isolation work is deliberately absent. A heavy set of calf raises or leg
# extensions is not what compromises a threshold session, and a guard that moved
# the plan for it would be wrong far more often than right. The claim being
# encoded is about large eccentric and systemic loading, not about any exercise
# a leg is involved in.
LOWER_BODY_MARKERS: tuple[str, ...] = (
    "squat",
    "deadlift",
    "rdl",
    "romanian",
    "good morning",
    "lunge",
    "leg press",
    "step up",
    "stepup",
    "step-up",
    "hip thrust",
    "glute bridge",
    "trap bar",
    "box jump",
)

# When a prescription counts as heavy. Both are read from the prescription's own
# intensity unit (#714) rather than inferred: RIR is reps left in reserve, so a
# *low* number is close to failure, and %e1RM is a fraction of the athlete's
# estimated maximum.
#
# RIR 3 and 75 % are the conventional boundary of "working heavy" in strength
# coaching, and they are the same boundary from two directions — a set of five
# at RIR 3 lands near 80 % for most lifters.
HEAVY_RIR_MAX = 3
HEAVY_PERCENT_E1RM_MIN = 75.0

# How far apart a gym session and an endurance session on the same date have to
# be. Six hours is the figure the interference literature converges on for
# same-day sessions; below it the two are effectively one session as far as the
# competing adaptive signals are concerned.
MIN_SEPARATION_HOURS = 6.0

# Directional weighting: endurance before strength costs more than strength
# before endurance, because interference runs more strongly in that direction.
# Used to *rank* findings and to word them, not to decide whether they fire —
# every rule here is already a fact about the plan, and a fact does not need a
# threshold. See :func:`interference_weight`.
_DIRECTION_ENDURANCE_FIRST = 1.0
_DIRECTION_STRENGTH_FIRST = 0.7

# Modality weighting: running interferes more than cycling. An unknown or
# unplannable sport weighs as cycling rather than as the worst case, so a sport
# this app cannot read never inflates a finding.
MODALITY_WEIGHT: dict[str, float] = {SPORT_RUNNING: 1.0, SPORT_CYCLING: 0.6}
_DEFAULT_MODALITY_WEIGHT = MODALITY_WEIGHT[SPORT_CYCLING]


def _parse_date(raw: object) -> date | None:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def is_lower_body_exercise(name: str | None) -> bool:
    """Whether this exercise loads the legs in the way the guard cares about."""
    normalized = " ".join((name or "").strip().casefold().split())
    if not normalized:
        return False
    return any(marker in normalized for marker in LOWER_BODY_MARKERS)


def _prescription_is_heavy(entry: dict) -> bool:
    """Whether a prescription states an intensity that counts as heavy.

    ``False`` when it states none. A prescription with neither RIR nor %e1RM is
    not evidence of light work either — it is a prescription that did not say,
    and "3×8 squat" is as likely to be a hard triple-digit set as a technique
    drill. Reading silence as heavy would revert plan writes on a guess.
    """
    rir = entry.get("rir")
    if isinstance(rir, bool):
        rir = None
    if isinstance(rir, (int, float)) and rir <= HEAVY_RIR_MAX:
        return True
    percent = entry.get("percentE1rm")
    if percent is None:
        percent = entry.get("percent_e1rm")
    if isinstance(percent, bool):
        percent = None
    return isinstance(percent, (int, float)) and percent >= HEAVY_PERCENT_E1RM_MIN


def heavy_lower_body_lifts(day: object) -> list[str]:
    """The prescribed lifts on ``day`` that are both lower-body and heavy.

    Empty for every day that is not a gym session, every gym session with no
    prescription, and every prescription that named no intensity — see
    :func:`_prescription_is_heavy` for why silence is not read as heavy.
    """
    if not isinstance(day, dict):
        return []
    if day_sport(day) != SPORT_STRENGTH:
        return []
    entries = day.get("strengthExercises")
    if entries is None:
        entries = day.get("strength_exercises")
    if not isinstance(entries, list):
        return []
    lifts: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("exercise")
        if not isinstance(name, str) or not is_lower_body_exercise(name):
            continue
        if _prescription_is_heavy(entry):
            lifts.append(" ".join(name.strip().casefold().split()))
    return lifts


def is_strength_session(day: object) -> bool:
    """Whether this session is gym work, in the shared sport vocabulary (#710).

    Read through ``day_sport`` rather than ``workoutType`` so a day that states
    ``sport: "strength"`` counts, and so does a pre-#710 day whose only sport
    marker was ``workoutType: "strength"``.
    """
    return isinstance(day, dict) and day_sport(day) == SPORT_STRENGTH


def is_key_endurance_session(day: object) -> bool:
    """Whether this session is an endurance session whose quality is the point."""
    if not isinstance(day, dict):
        return False
    if day_sport(day) not in _ENDURANCE_SPORTS:
        return False
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "")
    return workout_type.strip().lower() in KEY_WORKOUT_TYPES


def _is_endurance_session(day: dict) -> bool:
    """Any loading ride or run, key or not. Private, and dict-only.

    Wider than :func:`is_key_endurance_session` because the separation rule is
    about a competing adaptive signal rather than about how hard the ride was.
    Takes a dict without checking, unlike the public predicates above: its only
    caller gets its sessions from :func:`_sessions_in_window`, which has already
    dropped everything that is not one.
    """
    if day_sport(day) not in _ENDURANCE_SPORTS:
        return False
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "")
    return workout_type.strip().lower() not in ("rest", "off", "")


_CLOCK = re.compile(
    r"^(?P<hour>\d{1,2})(?:[:.h](?P<minute>\d{2}))?\s*(?P<suffix>am|pm)?$"
)


def minutes_into_day(time_of_day: object) -> int | None:
    """Read ``timeOfDay`` as a clock time, or ``None`` if it is not one.

    ``time_of_day`` is free text and advisory by design ("am", "pm", "18:30") —
    ``slot`` is the identity and the ordering. So only an actual clock time can
    answer "how many hours apart are these two sessions". "morning" and
    "evening" are *not* converted to notional hours: inventing 09:00 and 18:00
    for them would manufacture a nine-hour separation the athlete never stated,
    and then clear or fire the separation guard on a number nobody wrote.

    A bare hour under twelve with neither minutes nor a suffix is refused for
    the same reason. "6" is as likely to be the evening as the morning, and
    reading it as 06:00 would turn a four-hour gap into a twelve-hour one and
    quietly clear the guard. Writing the minutes ("6:00", "06.00") states the
    24-hour convention every other time in this app uses, and is read as such.
    """
    if isinstance(time_of_day, bool) or not isinstance(time_of_day, (str, int, float)):
        return None
    match = _CLOCK.match(str(time_of_day).strip().casefold())
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    if minute > 59:
        return None
    suffix = match.group("suffix")
    if match.group("minute") is None and suffix is None and hour < 12:
        return None
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    if hour > 23:
        return None
    return hour * 60 + minute


def interference_weight(finding: dict) -> float:
    """How much this finding costs the athlete, relative to the others.

    The directional and modality findings of the interference literature, used
    for what they can honestly be used for: ranking. The worst pair is stated to
    the coach first, and when one write creates two findings competing for the
    same session the heavier one decides.

    They are deliberately *not* a firing threshold. Every rule in this module is
    decidable from the plan — a weight gating it would turn a fact into a
    tunable, and the tuning would be the author's taste rather than the
    athlete's data.
    """
    sport = finding.get("sport")
    modality = MODALITY_WEIGHT.get(str(sport or ""), _DEFAULT_MODALITY_WEIGHT)
    direction = (
        _DIRECTION_ENDURANCE_FIRST
        if finding.get("rule") == RULE_INSUFFICIENT_SEPARATION
        else _DIRECTION_STRENGTH_FIRST
    )
    return direction * modality


def _sessions_in_window(
    plan: list[dict] | None, today: str | None, horizon_days: int
) -> dict[date, list[dict]]:
    """Plan sessions by date, from today forward, slot-ordered within each date."""
    start = _parse_date(today)
    by_date: dict[date, list[dict]] = {}
    for day in plan or []:
        if not isinstance(day, dict):
            continue
        parsed = _parse_date(day.get("date"))
        if parsed is None or (start is not None and parsed < start):
            continue
        if start is not None and (parsed - start).days > horizon_days:
            continue
        by_date.setdefault(parsed, []).append(day)
    for sessions in by_date.values():
        sessions.sort(key=day_slot)
    return by_date


def _finding(
    rule: str,
    *,
    strength: dict,
    endurance: dict,
    lifts: list[str] | None = None,
    hours_apart: float | None = None,
) -> dict:
    finding = {
        "rule": rule,
        "strength_date": str(strength.get("date") or ""),
        "strength_slot": day_slot(strength),
        "strength_title": strength.get("title") or "",
        "key_date": str(endurance.get("date") or ""),
        "key_slot": day_slot(endurance),
        "key_title": endurance.get("title") or "",
        "key_workout_type": endurance.get("workoutType")
        or endurance.get("workout_type"),
        "sport": day_sport(endurance),
        "lifts": list(lifts or []),
        "hours_apart": hours_apart,
    }
    finding["weight"] = interference_weight(finding)
    return finding


def find_heavy_legs_before_key(
    plan: list[dict] | None,
    today: str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict]:
    """Heavy lower-body work on the calendar day before a key endurance session.

    The one rule in this module that #714 made decidable. The prescribed lifts
    say whether the gym session loads the legs, and the intensity unit says
    whether it loads them heavily; without both the day is skipped rather than
    guessed at.

    Only the day *immediately* before. Two days out the legs have recovered
    enough that the session is the session that was prescribed, and a guard
    reaching further would start rejecting ordinary weeks.
    """
    by_date = _sessions_in_window(plan, today, horizon_days)
    findings: list[dict] = []
    for current in sorted(by_date):
        following = by_date.get(current + timedelta(days=1))
        if not following:
            continue
        key_sessions = [d for d in following if is_key_endurance_session(d)]
        if not key_sessions:
            continue
        for session in by_date[current]:
            lifts = heavy_lower_body_lifts(session)
            if not lifts:
                continue
            for key_session in key_sessions:
                findings.append(
                    _finding(
                        RULE_HEAVY_LEGS_BEFORE_KEY,
                        strength=session,
                        endurance=key_session,
                        lifts=lifts,
                    )
                )
    return findings


def find_strength_before_intensity(
    plan: list[dict] | None,
    today: str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict]:
    """A gym session scheduled ahead of the same day's key endurance session.

    Ordering, not spacing: the quality session is the one the week is built
    around, and it goes first while the athlete can still produce it. Lifting
    first turns the interval session into an interval session done tired, which
    is a different session with a different adaptation.

    Decided from ``slot``, which is the session's identity and its order within
    the date (#496) — so this fires on every two-a-day regardless of whether the
    athlete wrote a ``timeOfDay``.
    """
    by_date = _sessions_in_window(plan, today, horizon_days)
    findings: list[dict] = []
    for current in sorted(by_date):
        sessions = by_date[current]
        for index, session in enumerate(sessions):
            if not is_strength_session(session):
                continue
            for later in sessions[index + 1 :]:
                if is_key_endurance_session(later):
                    findings.append(
                        _finding(
                            RULE_STRENGTH_BEFORE_INTENSITY,
                            strength=session,
                            endurance=later,
                        )
                    )
    return findings


def find_insufficient_separation(
    plan: list[dict] | None,
    today: str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict]:
    """A gym session and an endurance session too close together on one date.

    The direction this one covers is the expensive one: endurance work in the
    hours before lifting is what blunts the strength adaptation, and six hours is
    where the literature puts the boundary. Any endurance session counts here,
    not only a key one — the interference is a competing adaptive signal, not a
    question of how hard the ride was.

    Fires only when *both* sessions carry a readable clock time. "morning" and
    "afternoon" are not hours (see :func:`minutes_into_day`), and a date where
    nobody wrote a time is a date this rule has nothing to say about. It is
    therefore the narrowest of the three by design — the ordering rule above is
    what catches the same two-a-day when the times are absent.
    """
    by_date = _sessions_in_window(plan, today, horizon_days)
    findings: list[dict] = []
    for current in sorted(by_date):
        timed = [
            (minutes_into_day(d.get("timeOfDay") or d.get("time_of_day")), d)
            for d in by_date[current]
        ]
        timed = [(minute, d) for minute, d in timed if minute is not None]
        for minute, session in timed:
            if not is_strength_session(session):
                continue
            for other_minute, other in timed:
                if other is session or not _is_endurance_session(other):
                    continue
                hours_apart = abs(minute - other_minute) / 60.0
                if hours_apart >= MIN_SEPARATION_HOURS:
                    continue
                findings.append(
                    _finding(
                        RULE_INSUFFICIENT_SEPARATION,
                        strength=session,
                        endurance=other,
                        hours_apart=round(hours_apart, 1),
                    )
                )
    return findings


def find_interference(
    plan: list[dict] | None,
    today: str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict]:
    """Every interference finding in the window, heaviest first.

    The order is :func:`interference_weight`'s only real job: the coach reads
    the worst pair first, and a write that creates two findings competing for
    one session resolves to the heavier.
    """
    findings = [
        *find_heavy_legs_before_key(plan, today, horizon_days=horizon_days),
        *find_strength_before_intensity(plan, today, horizon_days=horizon_days),
        *find_insufficient_separation(plan, today, horizon_days=horizon_days),
    ]
    findings.sort(
        key=lambda f: (
            -f["weight"],
            f["strength_date"],
            f["strength_slot"],
            f["key_date"],
            f["key_slot"],
            f["rule"],
        )
    )
    return findings


def finding_key(finding: dict) -> tuple:
    """A stable identity for one finding, for the gate's before/after comparison.

    Structural — the two sessions and the rule — and deliberately *not* carrying
    the lifts or the hour gap. A conflict that was already in the plan stays the
    same conflict when a write swaps one heavy squat variant for another, and
    reverting the write for it would punish it for someone else's mess. This is
    the one place this module parts company with
    ``plan_coherence.collision_keys``, which does carry the session signature:
    there, two days holding a *different* duplicate session genuinely is a new
    collision, because the duplication is the whole finding.
    """
    return (
        finding["rule"],
        finding["strength_date"],
        finding["strength_slot"],
        finding["key_date"],
        finding["key_slot"],
    )


def interference_keys(
    plan: list[dict] | None,
    today: str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> dict[tuple, dict]:
    """Every finding in the window, keyed by :func:`finding_key`.

    A mapping rather than a set (unlike ``plan_coherence.collision_keys``) because
    the gate needs the finding itself once it has decided one is new: the
    rationale it records names the lifts and the session, and re-deriving them
    from the key would be re-running the detection.
    """
    return {
        finding_key(finding): finding
        for finding in find_interference(plan, today, horizon_days=horizon_days)
    }


def _lift_list(lifts: list[str]) -> str:
    if len(lifts) == 1:
        return lifts[0]
    return ", ".join(lifts[:-1]) + " and " + lifts[-1]


def finding_statement(finding: dict) -> str:
    """What is wrong with this pair, and the reason it is wrong.

    Factual and claim-free about the remedy: the detection does not decide what
    to do about a finding, so it must not say. The pipeline gate appends which
    session it gave back (see ``plan_pipeline._revert_new_interference``), and
    the coach's prompt section appends nothing at all, because there the finding
    is information the coach weighs rather than a correction already made.
    """
    sport = finding.get("sport") or SPORT_CYCLING
    rule = finding.get("rule")
    key_type = finding.get("key_workout_type") or "key"
    if rule == RULE_HEAVY_LEGS_BEFORE_KEY:
        lifts = _lift_list(finding.get("lifts") or ["heavy lower-body work"])
        return (
            f"Heavy lower-body work ({lifts}) on {finding['strength_date']} sits the "
            f"day before the {key_type} {sport} session on {finding['key_date']}. "
            "Legs loaded that hard cannot produce the session's power, so what the "
            "athlete rides is not the session that was prescribed."
        )
    if rule == RULE_STRENGTH_BEFORE_INTENSITY:
        return (
            f"The gym session on {finding['strength_date']} is scheduled ahead of the "
            f"same day's {key_type} {sport} session. The quality session goes first, "
            "while the athlete can still produce it; lifting first makes it a "
            "quality session done tired, which trains something else."
        )
    hours = finding.get("hours_apart")
    gap = f"{hours} h" if hours is not None else "less than six hours"
    return (
        f"The {sport} session and the gym session on {finding['strength_date']} are "
        f"{gap} apart. Endurance work in the hours before lifting is the direction "
        "interference runs most strongly, so the two need at least "
        f"{int(MIN_SEPARATION_HOURS)} h between them, or separate days."
    )
