"""A session that names a power zone gets that zone's watts (ai-trainer-ops#75).

The plan writer sets the title and description in prose and the power target as
numbers, separately, and the two drifted apart: a session described as Z2 came
with a 110–145 W target, which at the athlete's FTP is recovery. The athlete had
to ask three times what the session was. The words are what the athlete reads
and rides by, so when a session names exactly one zone and its target misses
that zone entirely, the target is set to the zone.

The same holds for the FTP the words name (ai-trainer-ops#147): every target is
computed from the profile's FTP, so a session text giving another figure tells
the athlete two different things.
"""

import re

from services.analysis import power_zone_boundaries

# A watt figure that is the FTP's: after "FTP" with only the words that say what
# it is ("FTP auf 300 W", "FTP liegt jetzt bei 300 W", "FTP of 285 W"), or right
# before it ("285 W FTP"). Not "die Einheit auf 180 W (60 % deiner FTP)", "225 W
# (75 % FTP)" or "an deine FTP angepasst, also 225 W" (reviews of #934).
FTP_WATTS = re.compile(
    r"\bftp\b(?:\s*[:=]|\s+(?:is|was|now|of|at|to|from|von|auf|bei|liegt|ist|jetzt|neu|new)\b)"
    r"{0,3}\s*(\d{2,4})\s*(?:w|watts?)\b"
    r"|\b(\d{2,4})\s*(?:w|watts?)[\s-]+ftp\b",
    re.IGNORECASE,
)


# A figure that follows an FTP figure as the change it names: "FTP von 250 W auf
# 300 W", "was 250 W, now 300 W", "from 250 W to 300 W" (review of #934).
FTP_NEXT_WATTS = re.compile(
    r"\s*,?\s*(?:auf|to|now|jetzt|then|dann|→|->)\s+(\d{2,4})\s*(?:w|watts?)\b",
    re.IGNORECASE,
)

# The day's fields the athlete reads as prose.
_TEXT_FIELDS = ("title", "description", "coachFeedback", "workoutPurpose")


def ftp_figures(sentence: str) -> set[int]:
    """The FTP figures ``sentence`` gives: for each figure beside the word, the
    last one chained to it, which is the FTP it says the athlete has now. "Von
    250 W auf 300 W" says 300, so 250 on the profile is a claim (review of
    #934)."""
    figures: set[int] = set()
    for match in FTP_WATTS.finditer(sentence):
        figure = int(match.group(1) or match.group(2))
        end = match.end()
        while chained := FTP_NEXT_WATTS.match(sentence, end):
            figure = int(chained.group(1))
            end = chained.end()
        figures.add(figure)
    return figures


def _profile_ftp_in(text: str, ftp: int) -> str:
    """``text`` with each FTP figure it gives replaced by ``ftp``.

    A figure chained to another ("von 250 W auf 300 W") names a change, not the
    FTP the targets use, and is left as written.
    """

    def replace(match: re.Match) -> str:
        group = 1 if match.group(1) else 2
        if int(match.group(group)) == ftp or FTP_NEXT_WATTS.match(text, match.end()):
            return match.group(0)
        start, end = (index - match.start() for index in match.span(group))
        return f"{match.group(0)[:start]}{ftp}{match.group(0)[end:]}"

    return FTP_WATTS.sub(replace, text)


def with_profile_ftp(day: dict, ftp: float | None) -> dict:
    """The day with every FTP figure its words give replaced by the profile's.

    The coach review found the plan text naming an FTP of 320 W for months,
    each write copying the figure from a day written before, while the rides
    were scored with 300 W (ai-trainer-ops#147). A day whose words already
    agree is returned as it is.
    """
    if not ftp or ftp <= 0:
        return day
    watts = round(ftp)
    fixed = {
        key: _profile_ftp_in(day[key], watts)
        for key in _TEXT_FIELDS
        if isinstance(day.get(key), str)
    }
    points = day.get("keyFocusPoints")
    if isinstance(points, list):
        fixed["keyFocusPoints"] = [
            _profile_ftp_in(point, watts) if isinstance(point, str) else point
            for point in points
        ]
    changed = {key: value for key, value in fixed.items() if value != day.get(key)}
    return {**day, **changed} if changed else day


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
    zones = power_zone_boundaries(ftp)
    if not zones:  # a non-positive FTP has no zones to move into
        return day
    bounds = zones[zone - 1]
    if high >= bounds["low_w"] and low <= bounds["high_w"]:
        return day
    return {
        **day,
        "targetPower": {**target, "low": bounds["low_w"], "high": bounds["high_w"]},
    }
