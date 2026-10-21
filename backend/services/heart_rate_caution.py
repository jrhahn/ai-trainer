"""A medication that blunts the heart rate turns heart-rate targets off (ai-trainer-ops#184).

Werner, 58, wrote "Ich nehme Betablocker gegen Bluthochdruck. Mein Puls geht
kaum noch über 140." The coach answered that heart-rate limits were no longer a
reliable compass, and the plan kept eight sessions with bpm targets from the max
HR he gave at onboarding: the plan contradicted the reply on a safety point.

So the statement becomes a flag on the profile (no drug name, no dose: only that
heart rate is not a target), and while it holds every plan write drops the
heart-rate targets. The athlete sees a chip under the reply and can take it back.
Pure: no database, no clock.
"""

from __future__ import annotations

import re

_DRUGS = (
    r"beta[\s-]*blocker\w*|betablocker\w*|β[\s-]*blocker\w*|"
    r"bisoprolol|metoprolol|atenolol|propranolol|nebivolol|carvedilol|sotalol|"
    r"verapamil|diltiazem|ivabradin\w*"
)
_TAKES = re.compile(
    rf"\b(?:{_DRUGS})\b",
    re.IGNORECASE,
)
# "Mein Puls geht kaum noch über 140", "my heart rate won't go above 130".
_CAPPED = re.compile(
    r"\b(?:puls|herzfrequenz|hf|heart\s*rate|hr|pulse)\b[^.!?\n]{0,30}?"
    r"\b(?:kaum|nicht|nie|barely|hardly|never|not|won'?t|doesn'?t|can'?t|cannot)\b"
    r"[^.!?\n]{0,20}?\b(?:über|ueber|above|over|past|beyond|higher\s+than|höher\s+als)\s*\d{2,3}",
    re.IGNORECASE,
)
# "Ich nehme keine Betablocker mehr", "I stopped taking beta blockers".
_STOPPED = re.compile(
    rf"(?:\b(?:keine?n?|kein)\s+(?:\w+\s+)?(?:{_DRUGS})\b[^.!?\n]{{0,20}}\bmehr\b"
    rf"|\b(?:{_DRUGS})\b[^.!?\n]{{0,30}}\b(?:abgesetzt|nicht\s+mehr)\b"
    rf"|\b(?:stopped|quit|no\s+longer|off)\b[^.!?\n]{{0,20}}\b(?:{_DRUGS})\b"
    rf"|\b(?:{_DRUGS})\b[^.!?\n]{{0,20}}\b(?:stopped|discontinued)\b)",
    re.IGNORECASE,
)
# A question about them, or someone else's, is not the athlete taking them.
_NOT_ME = re.compile(
    r"\?\s*$|\b(?:my\s+(?:wife|husband|father|mother|dad|mum|mom|friend)|"
    r"meine?\s+(?:frau|mann|vater|mutter|freund\w*))\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
# "Ich nehme keine Betablocker", "I don't take beta blockers": not taking them.
_NEGATED = re.compile(
    rf"\b(?:kein\w*|no|not|never|nie|don'?t\s+take|do\s+not\s+take)\b\s+(?:\w+\s+){{0,2}}(?:{_DRUGS})\b",
    re.IGNORECASE,
)


def _sentences(message: str) -> list[str]:
    return [m.group().strip() for m in _SENTENCE.finditer(message or "") if m.group().strip()]


def says_stopped(message: str) -> bool:
    """The athlete says they no longer take a heart-rate-lowering medication."""
    return any(_STOPPED.search(s) and not _NOT_ME.search(s) for s in _sentences(message))


def says_blunted(message: str) -> bool:
    """The athlete says their heart rate is blunted: a beta blocker or the like,
    or a pulse that will not rise past a number."""
    for sentence in _sentences(message):
        if _NOT_ME.search(sentence) or _STOPPED.search(sentence) or _NEGATED.search(sentence):
            continue
        if _TAKES.search(sentence) or _CAPPED.search(sentence):
            return True
    return False


# A sentence that sets a heart-rate target: "Keep your heart rate under 132
# bpm", "zwischen 133–145 Schlägen", "HR zone 2".
_HR_TARGET = re.compile(
    r"\b\d{2,3}\s*(?:(?:–|-|to|bis)\s*\d{2,3}\s*)?(?:bpm|schläge\w*|beats)\b"
    r"|\b(?:heart[\s-]*rate|hr|puls|herzfrequenz)[\s-]*zone\b"
    r"|\b(?:hr|herzfrequenz|puls)[\s-]*(?:z|zone)\s*\d\b",
    re.IGNORECASE,
)
_PIECE = re.compile(r"[^.!?;]+[.!?;]?\s*")


def _without_hr_sentences(text: object) -> object:
    if not isinstance(text, str) or not _HR_TARGET.search(text):
        return text
    kept = "".join(p.group() for p in _PIECE.finditer(text) if not _HR_TARGET.search(p.group()))
    return kept.strip()


def without_heart_rate_targets(day: dict) -> dict:
    """``day`` with no heart-rate target left: no ``targetHeartRate``, no
    interval paced by heart rate, no sentence or focus point naming a bpm."""
    if not isinstance(day, dict):
        return day
    cleaned = {k: v for k, v in day.items() if k not in ("targetHeartRate", "target_heart_rate")}
    intervals = day.get("intervals")
    if isinstance(intervals, list):
        cleaned["intervals"] = [
            {k: v for k, v in interval.items() if k not in ("heartRate", "heart_rate")}
            if isinstance(interval, dict)
            else interval
            for interval in intervals
        ]
    for key in ("description", "workoutPurpose"):
        if key in cleaned:
            cleaned[key] = _without_hr_sentences(cleaned[key])
    points = cleaned.get("keyFocusPoints")
    if isinstance(points, list):
        cleaned["keyFocusPoints"] = [p for p in points if not (isinstance(p, str) and _HR_TARGET.search(p))]
    return cleaned


def sets_heart_rate_target(day: dict) -> bool:
    """Whether ``day`` still steers by heart rate anywhere."""
    return without_heart_rate_targets(day) != day


# What the prompts are told instead of the max HR, while the flag holds.
PROMPT_NOTE = (
    "none: the athlete takes a medication that lowers the heart rate, so heart "
    "rate is no guide to intensity. Set no heart-rate targets and no zones from "
    "a max HR; steer by power where there is an FTP, and by perceived effort "
    "(RPE, talk test) otherwise."
)


def prompt_profile(profile: dict, heart_rate_targets_off: bool) -> dict:
    """``profile`` as the prompts get it: without a max HR to build zones from,
    and with the note, while heart-rate targets are off."""
    if not heart_rate_targets_off:
        return profile
    cleaned = {k: v for k, v in profile.items() if k not in ("maxHeartRate", "thresholdHeartRate")}
    cleaned["heartRateTargets"] = PROMPT_NOTE
    return cleaned
