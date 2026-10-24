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
# "Mein Puls geht kaum noch über 140", "my heart rate won't go above 130": a
# state. "Mein Puls soll nicht über 150 gehen" is a limit the athlete wants,
# not one a drug sets (review of #998).
# Modal and imperative forms only: "bleibt kaum über 130" and "will not go
# over 140" are states, and missing one leaves bpm targets in a beta-blocker
# user's plan (review of #998).
_WISH = re.compile(
    r"\b(?:soll\w*|darf|dürfen|möchte\w*|want\w*|should|shall|must|keep|"
    r"halte\s+(?:ihn|meinen|deinen|den))\b",
    re.IGNORECASE,
)
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
# "I stopped metoprolol, now I take bisoprolol": two statements in one sentence.
_CLAUSE = re.compile(r"[,;]|\s+(?:now|jetzt|but|aber|instead|stattdessen|dafür)\s+", re.IGNORECASE)
# "Ich nehme keine Betablocker", "I don't take beta blockers": not taking them.
_NEGATED = re.compile(
    rf"\b(?:kein\w*|no|not|never|nie|don'?t\s+take|do\s+not\s+take)\b\s+(?:\w+\s+){{0,2}}(?:{_DRUGS})\b",
    re.IGNORECASE,
)


def _sentences(message: str) -> list[str]:
    return [m.group().strip() for m in _SENTENCE.finditer(message or "") if m.group().strip()]


def _clauses(message: str) -> list[tuple[str, str, str]]:
    """Each clause with the sentence it is in (whose it is, and whether it is a
    question, belongs to the sentence) and the clause before it."""
    found = []
    for sentence in _sentences(message):
        previous = ""
        for clause in _CLAUSE.split(sentence):
            if clause.strip():
                found.append((clause.strip(), sentence, previous))
                previous = clause.strip()
    return found


# "Ich möchte, dass mein Puls …": the clause the wish is in ends where the
# wished-for one starts with "dass" or "that".
_COMPLEMENT = re.compile(r"^\s*(?:dass|daß|that|damit|so\s+that)\b", re.IGNORECASE)


def _blunted_clause(clause: str, sentence: str, previous: str = "") -> bool:
    if _NOT_ME.search(sentence) or _STOPPED.search(clause) or _NEGATED.search(clause):
        return False
    if _TAKES.search(clause):
        return True
    # A wish in another clause leaves this one a statement: "Mein Puls geht kaum
    # über 140, ich möchte trotzdem intensiv trainieren" (review of #998). Only
    # a clause that is the wish's object ("…, dass mein Puls …") is the wish.
    wished = bool(_WISH.search(clause)) or bool(
        previous and _COMPLEMENT.search(clause) and _WISH.search(previous)
    )
    return bool(_CAPPED.search(clause)) and not wished


def says_stopped(message: str) -> bool:
    """The athlete says they no longer take a heart-rate-lowering medication,
    and takes no other: "stopped metoprolol, now on bisoprolol" is not stopping."""
    if says_blunted(message):
        return False
    return any(
        _STOPPED.search(clause) and not _NOT_ME.search(sentence)
        for clause, sentence, _previous in _clauses(message)
    )


def says_blunted(message: str) -> bool:
    """The athlete says their heart rate is blunted: a beta blocker or the like,
    or a pulse that will not rise past a number."""
    return any(
        _blunted_clause(clause, sentence, previous) for clause, sentence, previous in _clauses(message)
    )


# A sentence that sets a heart-rate target: "Keep your heart rate under 132
# bpm", "zwischen 133–145 Schlägen", "HR zone 2".
_HR_WORD = r"(?:heart[\s-]*rate|hr|hf|puls|pulse|herzfrequenz)"
_HR_TARGET = re.compile(
    r"\b\d{2,3}\s*(?:(?:–|-|to|bis)\s*\d{2,3}\s*)?(?:bpm|schläge\w*|beats)\b"
    rf"|\b{_HR_WORD}[\s-]*(?:z|zone)\s*\d\b|\b{_HR_WORD}[\s-]*zone\b"
    # "Keep HR below 130", "HF unter 130", "heart rate between 120 and 135".
    # "1 hr at 200 W" is an hour, not a heart rate (review of #998).
    rf"|(?<!\d)(?<!\d\s)\b{_HR_WORD}\b[^.!?;]{{0,20}}?\b(?:below|under|above|over|between|at|around|"
    r"unter|über|ueber|zwischen|bei|um|bis|max(?:imal|imum)?)\s*\d{2,3}\b"
    r"(?!\s*(?:w\b|watt|rpm|min|km|%))"
    # "70 % of max HR", "75 % der max. HF".
    r"|\d{2,3}\s*(?:–\s*\d{2,3}\s*)?%\s*(?:of\s+(?:your\s+)?|der\s+|deiner\s+)?"
    rf"(?:max(?:imal|imum)?\.?\s*|maximal\w*\s+)?{_HR_WORD}\b"
    # "Zone 2 heart rate", "Puls-Zone 2" said the other way round.
    rf"|\bzone\s*\d\s*(?:\(\s*)?{_HR_WORD}\b",
    re.IGNORECASE,
)
# A sentence; the full stop of "max." or "ca." does not end one.
_PIECE = re.compile(r"(?:[^.!?;]|(?<=max)\.|(?<=ca)\.|\.(?=\d))+[.!?;]?\s*", re.IGNORECASE)
# Where a sentence's words end: its full stop and the space after it.
_PIECE_END = re.compile(r"[.!?;]?\s*$")
# A parenthesis with the space before it: "at 180–195 W (Tempo, 75–80% of your
# 245 W FTP, heart rate 131–145 bpm)".
_PARENTHESIS = re.compile(r"\s*\(([^()]*)\)")
# All that is left of a sentence that said nothing but a target: "Then."
_ONLY_A_LINK = re.compile(r"\W*(?:and|und|then|dann|but|aber|or|oder|so|also|danach)?\W*", re.IGNORECASE)
# A clause that now opens its sentence does not open with "and".
_LEADING_LINK = re.compile(r"^(?:and|und|but|aber|or|oder)\s+", re.IGNORECASE)
# A session's length, blocks and watts as its words give them: "75 minutes",
# "12-minute", "2 x 12", "180–195 W", "75–80%", "90 rpm".
_STRUCTURE = re.compile(
    r"\b\d+\s*[x×]\s*\d+"
    r"|\b\d+(?:[.,]\d+)?(?:\s*(?:–|-|to|bis)\s*\d+(?:[.,]\d+)?)?[\s-]*"
    r"(?:(?:min(?:ute[sn]?|uten)?|h|hours?|std|stunden?|sec(?:onds?)?|s|w|watt|rpm|km)\b|%)",
    re.IGNORECASE,
)


def _clauses_of(body: str) -> list[str]:
    """``body`` split at its commas, except one inside a parenthesis or a
    number ("1,5 h")."""
    parts, depth, start = [], 0, 0
    for i, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif char == "," and depth == 0 and not (
            0 < i < len(body) - 1 and body[i - 1].isdigit() and body[i + 1].isdigit()
        ):
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return parts


def _kept_in_parenthesis(match: re.Match[str]) -> str:
    """The parenthesis without its parts that set a heart-rate target, or
    nothing when those were all it held."""
    inner = match.group(1)
    if not _HR_TARGET.search(inner):
        return match.group(0)
    kept = [part.strip() for part in re.split(r"[,;]", inner) if part.strip() and not _HR_TARGET.search(part)]
    if not kept:
        return ""
    space = match.group(0)[: match.group(0).index("(")]
    return f"{space}({', '.join(kept)})"


def _without_hr_clauses(sentence: str) -> str:
    """``sentence`` without its parts that set a heart-rate target: a part of a
    parenthesis, or a clause between commas.

    "" when the target was all the sentence said, or when it stands in the
    sentence's main clause ("Ride 2 x 12 min at 200 W and 150 bpm."): there is
    no part to cut it from, and :func:`without_heart_rate_targets` then says
    the session's length and watts from its fields."""
    end = _PIECE_END.search(sentence)
    body, tail = sentence[: end.start()], sentence[end.start():]
    body = _PARENTHESIS.sub(_kept_in_parenthesis, body)
    clauses = _clauses_of(body)
    kept = [clause for clause in clauses if not _HR_TARGET.search(clause)]
    if len(kept) < len(clauses):
        body = ",".join(kept).strip()
        if _HR_TARGET.search(clauses[0]):
            body = _LEADING_LINK.sub("", body)
            body = body[:1].upper() + body[1:]
    if _HR_TARGET.search(body) or _ONLY_A_LINK.fullmatch(body):
        return ""
    return body + tail


def _without_hr_text(text: object) -> object:
    """``text`` with each heart-rate target cut out at the smallest part that
    holds it (ai-trainer-ops#227).

    Werner's tempo sessions gave their blocks, watts and bpm in one sentence:
    "… two 12-minute blocks at 180–195 W (Tempo, 75–80% of your 245 W FTP,
    keeping heart rate between 131–145 bpm), with 5 minutes of easy spinning
    between blocks …". Dropping the whole sentence left "This introduces
    controlled intensity." and, for the other one, nothing at all."""
    if not isinstance(text, str) or not _HR_TARGET.search(text):
        return text
    return "".join(
        _without_hr_clauses(piece.group()) if _HR_TARGET.search(piece.group()) else piece.group()
        for piece in _PIECE.finditer(text)
    ).strip()


def _length_text(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds % 60 == 0 else f"{seconds} s"


def _blocks_text(intervals: object, watts: str | None) -> str | None:
    """The intervals in words, repeats counted: "2 × 12 min at 187 W, 5 min
    easy between". A block without watts or a pace takes the session's."""
    if not isinstance(intervals, list):
        return None
    groups: list[list] = []
    for interval in intervals:
        if not isinstance(interval, dict):
            continue
        duration = interval.get("duration")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            continue
        rest = interval.get("rest")
        key = [duration, interval.get("power"), interval.get("pace"), rest if isinstance(rest, int) and rest > 0 else 0]
        if groups and groups[-1][1:] == key:
            groups[-1][0] += 1
        else:
            groups.append([1, *key])
    words = []
    for count, duration, power, pace, rest in groups:
        block = f"{count} × {_length_text(duration)}" if count > 1 else _length_text(duration)
        at = f"{power} W" if power else (pace or watts)
        if at:
            block += f" at {at}"
        if rest:
            block += f", {_length_text(rest)} easy {'between' if count > 1 else 'after'}"
        words.append(block)
    return ", then ".join(words) or None


def _structure_text(day: dict) -> str | None:
    """The session's length, blocks and watts from its fields, as one sentence;
    None when the fields hold none of them."""
    low, high, minutes = day.get("durationMinMinutes"), day.get("durationMaxMinutes"), day.get("durationMinutes")
    if low and high and low != high:
        length = f"{low}–{high} min"
    else:
        length = f"{minutes} min" if minutes else None
    power = day.get("targetPower")
    watts = None
    if isinstance(power, dict) and power.get("low") and power.get("high"):
        watts = f"{power['low']} W" if power["low"] == power["high"] else f"{power['low']}–{power['high']} W"
    blocks = _blocks_text(day.get("intervals"), watts)
    if blocks:
        return f"{length}: {blocks}." if length else f"{blocks}."
    if length:
        return f"{length} at {watts}." if watts else f"{length}."
    return None


def _lost_its_structure(before: object, after: object) -> bool:
    """Whether the strip took the session's length, blocks and watts out of
    its words: they named some and what is left names none, or nothing is
    left at all."""
    if not isinstance(before, str) or before == after:
        return False
    left = after if isinstance(after, str) else ""
    if not left.strip():
        return True
    return bool(_STRUCTURE.search(_HR_TARGET.sub(" ", before))) and not _STRUCTURE.search(left)


def without_heart_rate_targets(day: dict) -> dict:
    """``day`` with no heart-rate target left: no ``targetHeartRate``, no
    interval paced by heart rate, no clause or focus point naming a bpm.

    The words of the session stay: only the part that names the target goes
    (ai-trainer-ops#227). A description that would lose its length, blocks and
    watts with it, or be left empty, gets them back from the day's fields."""
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
            cleaned[key] = _without_hr_text(cleaned[key])
    if _lost_its_structure(day.get("description"), cleaned.get("description")):
        structure = _structure_text(cleaned)
        if structure:
            left = cleaned.get("description")
            cleaned["description"] = f"{structure} {left.strip() if isinstance(left, str) else ''}".strip()
    points = cleaned.get("keyFocusPoints")
    if isinstance(points, list):
        cleaned["keyFocusPoints"] = [kept for kept in (_without_hr_text(point) for point in points) if kept != ""]
    return cleaned


def sets_heart_rate_target(day: dict) -> bool:
    """Whether ``day`` still steers by heart rate anywhere: a target that is
    set (``None`` is no target), an interval paced by it, or words naming one."""
    if not isinstance(day, dict):
        return False
    if day.get("targetHeartRate") or day.get("target_heart_rate"):
        return True
    intervals = day.get("intervals")
    if isinstance(intervals, list) and any(
        isinstance(i, dict) and (i.get("heartRate") or i.get("heart_rate")) for i in intervals
    ):
        return True
    texts = [day.get("description"), day.get("workoutPurpose"), *(day.get("keyFocusPoints") or [])]
    return any(isinstance(t, str) and _HR_TARGET.search(t) for t in texts)


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
