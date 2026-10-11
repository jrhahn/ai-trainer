"""Which language the athlete writes, named for the coach (ai-trainer-ops#78).

#658 tells the coach to infer the athlete's language from their words. With a
16k-token English prompt and one short German line to infer from, it still
answered German questions in English. Working the language out here and naming
it ("The athlete writes German") leaves the model nothing to infer.

Only German and English are told apart, the two the app's athletes write; any
other language is left to the general rule. Function words decide, so names,
numbers and English training jargon in a German message ("3x12 sweet spot")
do not tip it.
"""

import re

_GERMAN = frozenset(
    """und der die das ist nicht ich du dich dein deine mein meine heute morgen mit
    für auf ein eine einen wir es zu den dem bei oder aber noch schon auch wie was
    soll kann habe hab bin war mir mich sind lieber eher""".split()
)
_ENGLISH = frozenset(
    """the and you your is not today tomorrow with for on a we it to of in at this
    that i my me should could can have had was am are or but still already how what
    rather""".split()
)
# Fewer function words than this and a message says too little on its own.
MIN_EVIDENCE = 3


def _counts(text: str) -> tuple[int, int]:
    words = re.findall(r"[a-zäöüß']+", text.casefold())
    return sum(w in _GERMAN for w in words), sum(w in _ENGLISH for w in words)


def athlete_language(message: str, earlier: list[str] | None = None) -> str | None:
    """ "German", "English" or None, from the message and, if it is too short, the
    athlete's earlier messages (most recent last)."""
    german, english = _counts(message)
    for text in reversed(earlier or []):
        if german + english >= MIN_EVIDENCE:
            break
        more_german, more_english = _counts(text)
        german, english = german + more_german, english + more_english
    if german + english < 2 or german == english:
        return None
    return "German" if german > english else "English"
