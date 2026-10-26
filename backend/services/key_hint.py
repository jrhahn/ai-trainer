"""What the app shows of a saved key: its first and last four characters (ai-trainer-ops#307).

Settings shows a saved key masked, with an eye that reveals this hint and never
the key. The athlete can tell which key is saved, and no response carries the
key itself, so "never returned in API responses" stays true.

The hint is cut from the key when a response is built, not stored beside it:
the key is decrypted on every request anyway, and a stored hint would keep
eight of its characters unencrypted in the database. A key saved before the
hint existed has one too.
"""

HINT_CHARS = 4
"""How many characters each end of the key shows."""

MIN_KEY_LENGTH = 20
"""A shorter key gets no hint: its two ends would be too much of it.

The keys the app takes are all longer: Gemini's have 39 characters, OpenAI's
more than 40, intervals.icu's about 25.
"""


def key_hint(key: str | None) -> str | None:
    """``AIza…3fQk`` for a saved key; ``None`` without one, or for a short one."""
    if not key or len(key) < MIN_KEY_LENGTH:
        return None
    return f"{key[:HINT_CHARS]}…{key[-HINT_CHARS:]}"
