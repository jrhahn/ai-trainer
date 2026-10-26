"""The key a stored text is found again by (ai-trainer-ops#249)."""

from __future__ import annotations


def normalise_key(text: str) -> str:
    """Case and spacing do not make a different text. The key keeps at most 255
    characters, the width of every ``*_key`` column it is stored in.

    The memory facts and the coach's hypotheses, open questions, inquiries,
    experiments and predictions each had a copy of this; a change to it had to
    be made six times.
    """
    return " ".join(text.casefold().split())[:255]
