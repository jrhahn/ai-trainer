"""A session that names a power zone gets that zone's watts (ai-trainer-ops#75).

The plan writer sets the title and description in prose and the power target as
numbers, separately, and the two drifted apart: a session described as Z2 came
with a 110–145 W target, which at the athlete's FTP is recovery. The athlete had
to ask three times what the session was. The words are what the athlete reads
and rides by, so when a session names exactly one zone and its target misses
that zone entirely, the target is set to the zone.
"""

import re

from services.analysis import power_zone_boundaries

_NAMED_ZONE = re.compile(r"\b(?:z|zone\s*)([1-7])\b", re.IGNORECASE)
# "keep it below Z2" names Z2 as a ceiling, not as the session.
_BOUNDING_WORD = re.compile(
    r"\b(?:below|under|unter|unterhalb|max|maximal|höchstens|not|nicht|kein|keine|above|über|oberhalb|from|von|to|bis)\s+(?:\w+\s+)?$",
    re.IGNORECASE,
)


def _named_zone(text: str) -> int | None:
    zones: set[int] = set()
    for match in _NAMED_ZONE.finditer(text):
        if _BOUNDING_WORD.search(text[: match.start()]):
            return None
        zones.add(int(match.group(1)))
    return zones.pop() if len(zones) == 1 else None


def align_target_to_named_zone(day: dict, ftp: float | None) -> dict:
    """The day with its power target moved into the zone its words name, if they disagree."""
    target = day.get("targetPower")
    if not ftp or not isinstance(target, dict) or day.get("intervals"):
        return day
    low, high = target.get("low"), target.get("high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return day
    zone = _named_zone(f"{day.get('title') or ''} {day.get('description') or ''}")
    # Z1 is open below and Z7 open above, so neither has a range to move into.
    if zone is None or not 2 <= zone <= 6:
        return day
    bounds = power_zone_boundaries(ftp)[zone - 1]
    if high >= bounds["low_w"] and low <= bounds["high_w"]:
        return day
    return {
        **day,
        "targetPower": {**target, "low": bounds["low_w"], "high": bounds["high_w"]},
    }
