"""An FTP the athlete states in the coach chat (ai-trainer-ops#70).

"My FTP is rather 285 W, just adapt the zones" used to get "I've updated your
FTP to 285W" from the coach and no write at all: no code path set
``users.current_ftp`` from chat, so every zone kept coming from the old value
for months while the coach believed it had changed it.

The capture is deterministic, like the other self-report captures in the chat
route: the athlete's own FTP (``my``/``mein``), a number with a watt unit, said
as a statement. Questions, past values, negations and anyone else's FTP are not
captured. A long message is skipped: it is almost always pasted text, where
"FTP ~300 W" is somebody else's estimate, not the athlete setting theirs.
"""

import re

from schemas import FTP_MAX_WATTS, FTP_MIN_WATTS

# ponytail: pasted conversations are told apart by length alone; a provenance
# flag on the message would be exact if the client ever sends one.
MAX_MESSAGE_CHARS = 600

_STATED = re.compile(
    r"\b(?:my|mein|meine)\s+ftp\b[^.?!\d\n]{0,30}?(?P<watts>\d{2,3})\s*(?:w|watt)\b",
    re.IGNORECASE,
)
_NOT_A_STATEMENT = re.compile(
    r"\b(?:was|were|used to|not|isn't|früher|vorher|war|nicht|kein|keine|if|wenn|ob)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.?!\n]+[.?!]?")
_CLAUSE_BREAK = re.compile(r"[,;:]")


def extract_stated_ftp(message: str) -> int | None:
    """The FTP the athlete says is theirs now, or None."""
    if not message or len(message) > MAX_MESSAGE_CHARS:
        return None
    for sentence in _SENTENCE.findall(message):
        if sentence.rstrip().endswith("?"):
            continue
        for clause in _CLAUSE_BREAK.split(sentence):
            match = _STATED.search(clause)
            # The clause up to the number: "if my FTP is 320 W" and "my FTP was
            # 320 W" carry the disqualifying word there, while "the meter was
            # off, my FTP is 285 W" keeps its "was" in the clause before.
            if match is None or _NOT_A_STATEMENT.search(clause[: match.end()]):
                continue
            watts = int(match.group("watts"))
            if FTP_MIN_WATTS <= watts <= FTP_MAX_WATTS:
                return watts
    return None
