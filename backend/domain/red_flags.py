"""Symptoms that end a training conversation and start a medical one (ai-trainer-ops#338).

Chest pain, fainting, palpitations, shortness of breath out of proportion to the
effort, or dizziness during exercise are warning signs that guidelines for
exercise in adults name as reasons to stop and see a doctor before training
again (ACSM's Guidelines for Exercise Testing and Prescription; ESC 2020
guidelines on sports cardiology). Until now only one prompt sentence handled
them: the model could answer "Mir ist schwindelig und ich habe Brustschmerzen
beim Bergauffahren" with an easier interval, and nothing checked it.

This only reads the athlete's message. It decides nothing medical: the app
says, in fixed words, to stop and have it checked, and the plan holds every
session until the athlete says a doctor has checked it
(``athlete_states.KIND_CLEARANCE``).

Pure: no database, no clock.
"""

from __future__ import annotations

import re

CHEST = "chest"
FAINT = "faint"
HEART = "heart"
BREATH = "breath"
DIZZY = "dizzy"

# A negation directly before the symptom, at most one word between: "keine
# Brustschmerzen", "no chest pain", "nicht schwindelig", "never fainted",
# "keine richtigen Brustschmerzen". Not across a clause: in "Ich habe nicht gut
# geschlafen. Brustschmerzen beim Anstieg" the "nicht" is about the sleep
# (review of #1239).
_NEGATION = re.compile(
    r"(?:\b(?:kein\w*|nicht|nie|ohne|no|not|never|without)\b|n't\b)"
    r"(?:[^\S\n]+[^\W\d_]+)?"
    # …and a filler after it: "did not get any chest pain".
    r"(?:[^\S\n]+(?:any|irgendwelche\w*|richtige\w*|wirklich\w*))?[^\S\n]*$",
    re.IGNORECASE,
)

_SYMPTOMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        CHEST,
        re.compile(
            r"\b(?:brust\s*schmerz\w*|brustschmerz\w*|schmerz\w*\s+in\s+der\s+brust"
            r"|druck\s+auf\s+der\s+brust|enge\s*gefühl\w*\s+in\s+der\s+brust"
            r"|engegefühl\w*\s+(?:in\s+der\s+)?brust|stechen\w*\s+in\s+der\s+brust"
            r"|chest\s+(?:pain|tightness|pressure)|pain\s+in\s+(?:my|the)\s+chest"
            r"|tight(?:ness)?\s+(?:in\s+)?(?:my\s+)?chest)",
            re.IGNORECASE,
        ),
    ),
    (
        FAINT,
        re.compile(
            r"\b(?:ohnmächtig|ohnmacht\w*|bewusstlos\w*|kollabiert|zusammengebrochen"
            r"|(?:ich\s+)?bin\s+(?:\w+\s+){0,5}umgekippt|schwarz\s+vor\s+(?:den\s+)?augen"
            r"|fainted|fainting|passed\s+out|blacked\s+out|black\s*out|collapsed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        HEART,
        re.compile(
            r"\b(?:herzrasen|herzstolpern|herzrhythmus\w*|herz\s+(?:rast|stolpert|setzt\s+aus)"
            r"|palpitations?|heart\s+(?:racing|was\s+racing|is\s+racing|skipp\w*|flutter\w*)"
            r"|irregular\s+heart\s*beat|racing\s+heart)\b",
            re.IGNORECASE,
        ),
    ),
    (
        BREATH,
        re.compile(
            r"\b(?:atemnot|keine\s+luft\s+(?:mehr\s+)?bekomm\w*|kaum\s+luft\s+bekomm\w*"
            r"|shortness\s+of\s+breath|could(?:n't|\s+not)\s+breathe|can(?:'t|not)\s+breathe)\b",
            re.IGNORECASE,
        ),
    ),
    (
        DIZZY,
        re.compile(
            # Not the bare noun: "Schwindel" is also a swindle.
            r"\b(?:schwind(?:e)?lig\w*|schwindelgefühl\w*|mir\s+(?:ist|war|wird|wurde)\s+schwind\w*"
            r"|dizzy|dizziness|light-?headed)\b",
            re.IGNORECASE,
        ),
    ),
)


def red_flag(message: str) -> str | None:
    """The first warning sign the athlete states, or ``None``.

    A symptom the athlete denies ("keine Brustschmerzen") is none.
    """
    text = message or ""
    for kind, pattern in _SYMPTOMS:
        for match in pattern.finditer(text):
            before = text[max(0, match.start() - 40) : match.start()]
            if not _NEGATION.search(before) and not _negated_compound(match):
                return kind
    return None


def _negated_compound(match: re.Match[str]) -> bool:
    """A symptom word that is itself negated: "schwindelfrei"."""
    tail = match.string[match.end() : match.end() + 5].lower()
    return tail.startswith("frei")


# The app's words, never the model's: under the reply, in the athlete's
# language. 112 is the emergency number across the EU.
NOTE = {
    "German": (
        "Brustschmerzen, Ohnmacht, Herzrasen, Atemnot oder Schwindel beim "
        "Training sind ein Grund, aufzuhören und das ärztlich abklären zu lassen, "
        "bevor du weitertrainierst. Bei akuten Beschwerden ruf den Notruf 112. "
        "Bis dahin plant dein Plan keine Einheiten. Sag mir Bescheid, wenn ein "
        "Arzt es abgeklärt hat."
    ),
    "English": (
        "Chest pain, fainting, a racing heart, shortness of breath or dizziness "
        "during exercise are a reason to stop and have it checked by a doctor "
        "before you train again. If it is acute, call 112. Until then your plan "
        "holds no sessions. Tell me once a doctor has checked it."
    ),
}


def note(german: bool) -> str:
    return NOTE["German" if german else "English"]
