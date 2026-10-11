"""The athlete saying they have no race, as a proposal to confirm (ai-trainer-ops#71).

A race entered at setup stays on the profile until the athlete changes it in
Settings, and for months the coach planned toward one the athlete had said was
off. Clearing it automatically on a chat sentence would delete a real goal on a
false match, so the owner chose a confirm step (#63, Q21): the chat reply offers
to remove the race and the athlete presses the button or not.
"""

import re

_NO_RACE = re.compile(
    r"\b(?:"
    r"(?:i\s+have\s+|i've\s+got\s+|there\s+is\s+|there's\s+)?no\s+(?:upcoming\s+|more\s+)?races?(?:\s+planned)?"
    r"|(?:not|no\s+longer)\s+(?:doing|racing|riding)\s+(?:the|that|any)\s+race"
    r"|(?:don'?t|do\s+not)\s+have\s+(?:any\s+)?(?:further\s+|more\s+)?races?(?:\s+events?)?"
    r"|kein(?:e|en)?\s+(?:rennen|wettkampf|wettkämpfe)"
    r"|(?:das\s+)?(?:rennen|race)\s+(?:ist\s+)?(?:abgesagt|gestrichen|vorbei|cancelled|canceled|off|over)"
    # "the goal over the winter" and "goal off-road" are not the goal being gone:
    # over/off/vorbei need the verb, only the unambiguous words may stand alone.
    r"|(?:\w+\s+)?(?:ziel|goal)\s+(?:ist\s+|is\s+)?(?:erst\s+mal\s+|erstmal\s+)?(?:weg|gestrichen|gone|dropped)"
    r"|(?:\w+\s+)?(?:ziel|goal)\s+(?:ist|is)\s+(?:erst\s+mal\s+|erstmal\s+)?(?:vorbei|over|off)"
    r")\b",
    re.IGNORECASE,
)


def says_no_race(message: str) -> bool:
    """A statement (not a question) that the athlete has no race ahead."""
    for sentence in re.split(r"(?<=[.!?\n])", message):
        if sentence.rstrip().endswith("?"):
            continue
        if _NO_RACE.search(sentence):
            return True
    return False
