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
    für auf ein eine einen wir es zu den dem bei oder aber noch schon auch wie
    soll kann habe hab bin war mir mich sind lieber eher von vom meiner meinem
    wäre würde sollte mal nur jetzt sehr bitte danke zum zur mach mache machen
    lass ja nein""".split()
)
_ENGLISH = frozenset(
    """the and you your is not today tomorrow with for on a we it to of in at this
    that i my me should could can have had am are or but still already how what
    rather would just now very please thanks make let yes""".split()
)
# Fewer function words than this and a message says too little on its own.
MIN_EVIDENCE = 3


def _counts(text: str) -> tuple[int, int]:
    words = re.findall(r"[a-zäöüß']+", text.casefold())
    return sum(w in _GERMAN for w in words), sum(w in _ENGLISH for w in words)


def athlete_language(message: str, earlier: list[str] | None = None) -> str | None:
    """ "German", "English" or None. ``earlier`` holds the athlete's previous
    messages, oldest first; they are read, newest first, only while the message
    itself does not decide."""
    german, english = _counts(message)
    # The message speaks for itself when it leans clearly one way: a short
    # German line after English turns is German.
    if german + english >= 2 and german != english:
        return "German" if german > english else "English"
    for text in reversed(earlier or []):
        if german + english >= MIN_EVIDENCE:
            break
        more_german, more_english = _counts(text)
        german, english = german + more_german, english + more_english
    if german + english < 2 or german == english:
        return None
    return "German" if german > english else "English"


def written_in(text: str) -> str | None:
    """ "German" or "English" for the language ``text`` is written in, or None
    when its function words do not decide (ai-trainer-ops#149).

    Unlike :func:`athlete_language` this reads what the coach wrote, which is
    long and quotes English plan titles ("Core and Upper Body Strength") in a
    German reply. So it asks for a clear lean, twice as many of one language's
    function words as of the other's.
    """
    german, english = _counts(text)
    if max(german, english) < MIN_EVIDENCE or 2 * min(german, english) > max(german, english):
        return None
    return "German" if german > english else "English"


def in_other_language(text: str, expected: str | None) -> bool:
    """Whether ``text`` is clearly in the other language than ``expected``.

    Every prompt that writes to the athlete names their language, and the model
    still answered a German athlete in English: an inquiry reply, a chat reply
    and a nightly narrative in the first four weeks of the coach review
    (ai-trainer-ops#149). A text whose function words do not decide is not held
    against it, nor is any text when the athlete's language is unknown.
    """
    found = written_in(text)
    return expected in ("German", "English") and found is not None and found != expected


# The browser's interface language (ai-trainer-ops#121). A new account has said
# nothing in the chat yet, so its summary and first replies had no language to
# go by and came out English after a German onboarding. The interface the
# athlete chose is the next best evidence; their own words still win.
LANGUAGE_HEADER = "x-app-language"
_INTERFACE_LANGUAGES = {"de": "German", "en": "English"}


def interface_language(request: object) -> str | None:
    """ "German" or "English" from the ``X-App-Language`` header, else None."""
    headers = getattr(request, "headers", None)
    value = headers.get(LANGUAGE_HEADER) if headers is not None else None
    if not value:
        return None
    return _INTERFACE_LANGUAGES.get(str(value).strip().lower()[:2])
