"""The coach's notes about an athlete (ai-trainer-ops#119).

How much of them a prompt carries, and which writer reply may replace them.
"""

from __future__ import annotations

import re

# How much of the notes a prompt carries. The writer keeps them under 400 words,
# about 2,600 characters in English and more in German, and the race calendar is
# merged in after them. The chat kept only the last 800 characters, so notes of
# 156 words already lost "Schedule constraints", which the writer puts first,
# while the athlete could read them in the app and took the coach to know them.
# Longer notes are cut at the end, visibly (``untrusted_text.mark``).
PROMPT_CHARS = 4000

# The headings the writer files the notes under (``prompts.update_memory_system``),
# and the race calendar the app merges in (``routers.users._RACE_MEMORY_HEADING``).
HEADINGS = (
    "Schedule constraints",
    "Fatigue & intensity response",
    "Hydration, fueling & heat response",
    "Preferred workout types",
    "Recurring issues",
    "Psychological training tendencies",
    "FTP & target context",
    "Race & event priorities",
    "Goals & motivations",
    "Race calendar",
)

# A reply this much shorter than the notes it replaces lost them; an edit does
# not halve them. Below ``SHARE_FROM_CHARS`` the notes are too short for a share
# to mean anything: one removed line may be half of them.
MIN_KEPT_SHARE = 0.5
SHARE_FROM_CHARS = 300


def _normalised(text: str) -> str:
    return text.lower().replace("&", "and")


# A heading opens a line, after any list, number or emphasis marks.
_HEADING = re.compile(
    r"^[ \t#*>\-\d.)]*(" + "|".join(re.escape(_normalised(h)) for h in HEADINGS) + r")\b",
    re.MULTILINE,
)


def headings_in(notes: str) -> set[str]:
    """The known headings that open a line of ``notes``."""
    return {match.group(1) for match in _HEADING.finditer(_normalised(notes))}


def rejection(current: str, updated: str | None) -> str | None:
    """Why ``updated`` may not replace ``current``, or None when it may.

    The writer's reply was stored whatever it was, so "I'm sorry, I can't help
    with that request." became an athlete's notes, with no history and no undo.
    A reply has to be notes: under at least one heading, keeping every heading
    the notes had, and not much shorter than them.
    """
    new = (updated or "").strip()
    old = (current or "").strip()
    if not new:
        return "empty"
    found = headings_in(new)
    if not found:
        return "no heading"
    if headings_in(old) - found:
        return "drops a heading"
    if len(old) >= SHARE_FROM_CHARS and len(new) < MIN_KEPT_SHARE * len(old):
        return "much shorter"
    return None
