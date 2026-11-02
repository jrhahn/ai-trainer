"""Tiredness that lasts gets a doctor's word, once (review of 2026-11-01,
training A7, ai-trainer-ops#338).

"Seit einer Woche bin ich ständig müde" gives three easy days, and saying it
again gives three more, indefinitely. The ECSS/ACSM consensus on overtraining
(Meeusen et al. 2013) asks to rule out medical causes of persistent fatigue
with lower performance (iron deficiency or anaemia, an infection, the thyroid,
low energy availability) before calling it training fatigue. The reply then
carries one fixed sentence: see a doctor for a blood check.

Due when tiredness for days comes with lower performance in the same message,
when the message says it has lasted weeks, or when the athlete first said it at
least ``LASTING_DAYS`` ago and says it again.

Pure: no database, no clock.
"""

from __future__ import annotations

import re

# From the first mention to one that still says it: two weeks less a few days,
# the review's "10 days". Older than ``LOOKBACK_DAYS``, a mention is another
# story.
LASTING_DAYS = 10
LOOKBACK_DAYS = 42

# "Meine Wattwerte sind schlecht", "die Leistung bricht ein", "my power is
# down", "I can't hit my numbers".
_PERFORMANCE_DOWN = re.compile(
    r"\b(?:"
    # No negation between: "Wattwerte sind nicht schlecht".
    r"(?:watt\w*|leistung|power|beine|form|pace|tempo)\s+"
    r"(?:(?!(?:nicht|kein\w*|not|no|never)\b)\w+\s+){0,3}?"
    r"(?:schlecht\w*|mies\w*|im\s+keller|bricht\s+ein|eingebrochen|weg|down|bad|poor"
    r"|dropp\w*|gone|terrible|falling|sinkt|gesunken)"
    r"|(?:schlecht\w*|miese?|schwache?)\s+(?:wattwerte|werte|leistung\w*|beine)"
    r"|(?:komme|kommen?)\s+(?:\w+\s+){0,2}nicht\s+(?:mehr\s+)?auf\s+(?:meine\s+)?(?:watt|werte|leistung)"
    r"|can'?t\s+(?:hit|reach|hold)\s+(?:my\s+)?(?:numbers|power|watts|targets)"
    r"|puls\s+(?:geht|kommt)\s+nicht\s+(?:mehr\s+)?hoch|heart\s+rate\s+won'?t\s+(?:go|come)\s+up"
    r")\b",
    re.IGNORECASE,
)
_NOT = re.compile(r"\b(?:nicht|kein\w*|not|no|never)\s+(?:\w+\s+)?$", re.IGNORECASE)
# "Seit Wochen müde", "seit drei Wochen", "for weeks", "for two weeks".
_WEEKS = re.compile(
    r"\b(?:seit\s+(?:(?:mehreren|einigen|vielen|zwei|drei|vier|\d{1,2})\s+)?wochen"
    r"|seit\s+(?:einem\s+)?monat(?:en)?"
    r"|for\s+(?:(?:several|many|a\s+few|two|three|four|\d{1,2})\s+)?weeks"
    r"|for\s+(?:a\s+)?months?)\b",
    re.IGNORECASE,
)


def says_performance_down(message: str) -> bool:
    """The message says the athlete's power or form has dropped."""
    for match in _PERFORMANCE_DOWN.finditer(message or ""):
        if not _NOT.search(message[max(0, match.start() - 25) : match.start()]):
            return True
    return False


def says_weeks(message: str) -> bool:
    """The message says the tiredness has lasted weeks, not days."""
    return bool(_WEEKS.search(message or ""))


NOTE = {
    "German": (
        "Wenn du dich schon länger müde fühlst und deine Leistung sinkt, lass das "
        "bitte ärztlich abklären, zum Beispiel mit einem Blutbild (Eisen, "
        "Schilddrüse, Entzündungswerte). Ist das unauffällig, kann es am Training "
        "liegen, und wir passen die Belastung an."
    ),
    "English": (
        "If you have felt tired for a while and your performance is dropping, "
        "please have it checked by a doctor, for example with a blood test "
        "(iron, thyroid, signs of infection). If that is clear, it may be the "
        "training, and we adjust the load."
    ),
}


def note(german: bool) -> str:
    return NOTE["German" if german else "English"]
