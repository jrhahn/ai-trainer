"""Age and health, as an older athlete brings them up (ai-trainer-ops#182).

The owner's test athlete wrote "given my age i want to stay healthy" and nobody
asked how old they were or how their health was. Three small, fixed answers:

- an age said in the chat ("ich bin 67") is offered as the profile's birth year;
- an age brought up without one, while the profile has none, gets a question;
- the first message to an athlete of 60 or more, or to one whose goal is to stay
  healthy, asks once about heart, blood pressure, joints and medication, and
  says when a doctor should look first.

Questions only: the coach does not diagnose (ai-trainer-ops#5).
"""

from __future__ import annotations

import re
from datetime import date

# From this age the first message asks about health. The ACSM's pre-participation
# screening asks about symptoms and known disease rather than age alone; age is
# where the odds of an unknown one rise, so the question goes out from here.
HEALTH_CHECK_AGE = 60

# The bounds of an age said in the chat, the profile's own (BIRTH_YEAR_MIN 1920,
# YOUNGEST_ATHLETE_YEARS 10) less a year's slack.
_YOUNGEST, _OLDEST = 10, 105

_STATED_AGE = re.compile(
    r"\b(?:i'?m|i\s+am|ich\s+bin)\s+(?:now\s+|jetzt\s+|schon\s+)?(\d{2,3})"
    r"\s*(?:years?(?:\s+old)?|yrs?(?:\s+old)?|jahre(?:\s+alt)?|j\.)"
    r"|\b(\d{2,3})\s*(?:years?\s+old|jahre\s+alt)\b",
    re.IGNORECASE,
)
_AGE_MENTION = re.compile(
    r"\b(?:given\s+my\s+age|at\s+my\s+age|for\s+my\s+age|my\s+age|"
    r"in\s+meinem\s+alter|mit\s+meinem\s+alter|f[üu]r\s+mein\s+alter|"
    r"in\s+my\s+(?:fifties|sixties|seventies|eighties|[5-9]0s)|"
    r"(?:ich\s+bin\s+)?(?:ü|ue)ber\s+(?:50|60|70|80)|"
    r"i'?m\s+(?:getting\s+)?(?:old|older)|ich\s+werde\s+(?:alt|älter))\b",
    re.IGNORECASE,
)

AGE_NOTE = {
    "German": (
        "Wie alt bist du? Schreib es mir oder trag dein Geburtsjahr in den "
        "Einstellungen ein. Dann plane ich die Erholung danach und frage, wo es "
        "darauf ankommt, nach deiner Gesundheit."
    ),
    "English": (
        "How old are you? Tell me, or enter your year of birth in Settings. Then "
        "I plan your recovery for it and ask about your health where that matters."
    ),
}

HEALTH_CHECK = {
    "German": (
        "Bevor wir loslegen: Gibt es etwas zu deiner Gesundheit, das ich wissen "
        "sollte? Zum Beispiel Herz oder Blutdruck, die Gelenke, oder ein "
        "Medikament, das den Puls verändert, wie ein Betablocker. Wenn du länger "
        "nicht aktiv warst oder unsicher bist, sprich vor dem Start mit deiner "
        "Ärztin oder deinem Arzt. Schreib es mir einfach hier."
    ),
    "English": (
        "Before we start: is there anything about your health I should know? For "
        "example your heart or blood pressure, your joints, or a medicine that "
        "changes your heart rate, such as a beta blocker. If you have not been "
        "active for a while or are unsure, talk to your doctor before you start. "
        "Just tell me here."
    ),
}


def stated_age(text: str) -> int | None:
    """The age ``text`` states ("I'm 67", "ich bin 67 Jahre alt"), or ``None``."""
    match = _STATED_AGE.search(text or "")
    if not match:
        return None
    age = int(match.group(1) or match.group(2))
    return age if _YOUNGEST <= age <= _OLDEST else None


def birth_year_from_age(age: int, today: date) -> int:
    """The year an athlete of ``age`` was born: right once their birthday has
    passed this year, a year late before it. The coach needs no more."""
    return today.year - age


def age_unasked(text: str, birth_year: int | None) -> bool:
    """Whether ``text`` brings up age without one, and the profile has none."""
    if birth_year:
        return False
    return bool(_AGE_MENTION.search(text or "")) and stated_age(text) is None


def age_note(german: bool) -> str:
    return AGE_NOTE["German" if german else "English"]


def health_check_due(goal: str | None, birth_year: int | None, today: date) -> bool:
    """Whether the first message asks about health: a health goal, or 60 and up."""
    if goal == "health":
        return True
    return bool(birth_year) and today.year - birth_year >= HEALTH_CHECK_AGE


def health_check(language: str | None) -> str:
    return HEALTH_CHECK["German" if language == "German" else "English"]
