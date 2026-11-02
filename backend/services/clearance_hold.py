"""A warning sign holds every session until a doctor has checked it (ai-trainer-ops#338, A1).

Chest pain, a racing heart, fainting, breathlessness or dizziness store a
clearance state (``chat_capture.stated_states``). As a prior it only reported:
in the model runs of 2026-11-02 the coach, rewritten once, still left sessions
in the hold in 6 of 10 chest-pain replies and 4 of 10 palpitation replies, and
said it had rested them all. The owner's decision of 2026-11-02: here the code
holds the sessions itself, an exception to "a prior, never a guard" (#39 Q1).

Every session from today to the state's end (``athlete_states.effective_end``,
14 days while no doctor has cleared it) becomes rest, whoever writes the plan,
except the athlete's own edit. A day done or ridden stays as it is. Pure: the
caller loads the states and says which days are done.
"""

from __future__ import annotations

from datetime import date, timedelta

from domain import athlete_states
import schemas
from services import rest_days

REASON = (
    "Paused until a doctor has checked the warning sign you described. Once "
    "you have been cleared, tell your coach and the training comes back."
)
PURPOSE = "Your health first: training waits for a doctor's all-clear."
FOCUS = (
    "See a doctor about the symptoms",
    "If chest pain, fainting or breathlessness comes back: call 112",
)
# Fixed words, so the German is fixed too (``plan_translations.FIXED``).
GERMAN = {
    REASON: (
        "Pausiert, bis ein Arzt das Warnzeichen abgeklärt hat, das du beschrieben "
        "hast. Sobald du freigegeben bist, sag es deinem Coach, dann kommt das "
        "Training zurück."
    ),
    PURPOSE: "Deine Gesundheit zuerst: Das Training wartet auf die ärztliche Freigabe.",
    FOCUS[0]: "Lass die Beschwerden ärztlich abklären",
    FOCUS[1]: "Kommen Brustschmerz, Ohnmacht oder Atemnot wieder: 112 rufen",
}


def held_dates(states: list[athlete_states.StatedState], today: date) -> set[str]:
    """The ISO dates from today on that an open clearance holds."""
    dates: set[str] = set()
    for state in states:
        if state.kind != athlete_states.KIND_CLEARANCE:
            continue
        end = athlete_states.effective_end(state)
        day = max(state.starts_on, today)
        while day <= end:
            dates.add(day.isoformat())
            day += timedelta(days=1)
    return dates


def hold(
    plan: list[dict],
    states: list[athlete_states.StatedState],
    *,
    today: date,
    done: set[str],
) -> tuple[list[dict], dict[tuple[str, int], str]]:
    """``plan`` with every session an open clearance holds turned into rest,
    and the reason per (date, slot) for the change history."""
    held = held_dates(states, today) - done
    if not held:
        return plan, {}
    result: list[dict] = []
    reasons: dict[tuple[str, int], str] = {}
    for day in plan:
        when = str(day.get("date"))
        if when in held and not day.get("completed") and not rest_days.is_rest(day):
            day = {
                **rest_days._as_rest(day, REASON),
                "workoutPurpose": PURPOSE,
                "keyFocusPoints": list(FOCUS),
            }
            reasons[schemas.session_key(day)] = REASON
        result.append(day)
    return result, reasons
