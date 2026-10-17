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
# What makes a sentence no statement of the athlete's FTP now (ai-trainer-ops#129).
# Doubt counts wherever it is in the sentence: "Ich glaube nicht, dass meine FTP
# 300 W ist" and "… 310 W, glaube ich aber nicht" were stored, because the check
# stopped at the comma.
_DOUBT = re.compile(
    r"\b(?:glaub\w*|denke|vermute\w*|bezweifl\w*|zweifl\w*|vielleicht|wahrscheinlich"
    r"|eventuell|möglicherweise|think|believe|guess|suppose|maybe|perhaps|probably|doubt\w*"
    r"|unsure|not\s+sure|nicht\s+sicher)\b",
    re.IGNORECASE,
)
# A negation counts in the FTP's own clause and after it: "Ich habe keine Zeit,
# meine FTP ist 250 W" is a statement (review of #934), "my FTP is not 320 W"
# and "… 310 W, glaube ich aber nicht" are not.
_NEGATION = re.compile(
    r"\b(?:not|no\s+longer|never|(?:do|does|did|is|are|was|were|ca|wo)n't|nicht|kein\w*|nie)\b",
    re.IGNORECASE,
)
# A past value or a condition counts in the FTP's own clause and after it, not
# in a clause before it: "my power meter was off, my FTP is 285 W" is a
# statement, "Meine FTP ist 285 W, aber das war letztes Jahr" is not.
_PAST_OR_CONDITION = re.compile(
    r"\b(?:was|were|used\s+to|früher|vorher|war|waren|damals|last\s+(?:year|season|spring|summer)"
    r"|letzte[ns]?\s+(?:jahr|saison|frühling|sommer)|if|whether|wenn|falls|ob)\b",
    re.IGNORECASE,
)
# "ca. 250 Watt" ended the sentence at "ca.".
_ABBREVIATION = re.compile(r"\b(ca|approx|zirka|circa|ungef)\.", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.?!\n]+[.?!]?")
# Only a break before "my FTP" starts its clause, so "Meine FTP: 250 W" keeps
# its colon.
_CLAUSE_BREAK = re.compile(r"[,;:]")


def extract_stated_ftp(message: str) -> int | None:
    """The FTP the athlete says is theirs now, or None."""
    if not message or len(message) > MAX_MESSAGE_CHARS:
        return None
    for sentence in _SENTENCE.findall(_ABBREVIATION.sub(r"\1", message)):
        if sentence.rstrip().endswith("?"):
            continue
        match = _STATED.search(sentence)
        if match is None:
            continue
        # The FTP's own clause starts after the last comma before it.
        clause_start = max(
            (m.end() for m in _CLAUSE_BREAK.finditer(sentence, 0, match.start())), default=0
        )
        if (
            _DOUBT.search(sentence)
            or _NEGATION.search(sentence, clause_start)
            or _PAST_OR_CONDITION.search(sentence, clause_start)
        ):
            continue
        watts = int(match.group("watts"))
        if FTP_MIN_WATTS <= watts <= FTP_MAX_WATTS:
            return watts
    return None
