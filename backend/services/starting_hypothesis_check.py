"""The first rides test what the coach assumed at the start (ai-trainer-ops#159).

A new athlete's starting assumptions (#154) are answered by tapping, or not at
all; nothing compared them with what the athlete then rode, and the regular
hypothesis engine only starts at eight activities. This step reads the rides
for the one assumption rides can settle on their own, the weekly volume, with
no model call.

- Rides that fit the stated range raise the assumption's confidence, quietly.
- Rides clearly outside it refute it, settle its pinned question, and post one
  short coach note that says what the rides show and points at rebuilding the
  plan (#158), in the language the question was asked in.

It runs after every sync and every upload, and is idempotent: a refuted
assumption is no longer open, so it is never refuted twice.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services.athlete_language import athlete_language
from services.dates import app_today
from services.starting_hypotheses import CATEGORY_PREFIX

logger = logging.getLogger(__name__)

VOLUME_CATEGORY = CATEGORY_PREFIX + "weekly_volume"
# How far back the rides are read, and the least span of riding that says
# anything about a week: two weeks, so one big weekend does not decide it.
WINDOW_DAYS = 28
MIN_SPAN_DAYS = 14
MIN_ACTIVITIES = 3
# "Clearly outside": a quarter beyond either end of the stated range.
MARGIN = 0.25
# Where rides that fit leave the assumption's confidence.
FITTING_CONFIDENCE = 0.6

_RANGE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:to|-|–)\s*(\d+(?:[.,]\d+)?)\s*h", re.IGNORECASE)
_UNDER = re.compile(r"(?:under|below|less than|fewer than)\s*(\d+(?:[.,]\d+)?)\s*h", re.IGNORECASE)
_OVER = re.compile(r"(?:over|above|more than)\s*(\d+(?:[.,]\d+)?)\s*h", re.IGNORECASE)


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def stated_range(statement: str) -> tuple[float, float] | None:
    """The hours-a-week range an assumption states, or None when it states none.

    ``(0, 3)`` for "under 3 hours", ``(6, inf)`` for "over 6 hours",
    ``(3, 6)`` for "3 to 6 hours".
    """
    if match := _RANGE.search(statement):
        low, high = sorted((_number(match.group(1)), _number(match.group(2))))
        return low, high
    if match := _UNDER.search(statement):
        return 0.0, _number(match.group(1))
    if match := _OVER.search(statement):
        return _number(match.group(1)), float("inf")
    return None


# A tapped answer is in the athlete's language: "unter 3 h", "Unter 3 Stunden",
# "3 bis 6 Stunden", "under 3 h".
_HOURS_UNIT = r"\s*(?:h\b|hrs?\b|hours?\b|std\b\.?|stunden?\b)"
_NUMBER = r"(\d+(?:[.,]\d+)?)"
_ANSWER_RANGE = re.compile(rf"{_NUMBER}\s*(?:to|bis|-|–)\s*{_NUMBER}{_HOURS_UNIT}", re.IGNORECASE)
_ANSWER_UNDER = re.compile(
    rf"\b(?:under|below|less than|fewer than|up to|unter|weniger als|bis zu)\s*{_NUMBER}{_HOURS_UNIT}",
    re.IGNORECASE,
)


def hours_cap(label: str) -> float | None:
    """The most hours a week a tapped answer states (ai-trainer-ops#236).

    3 for "unter 3 h" or "Unter 3 Stunden", 6 for "3–6 h" or "3 bis 6 Stunden",
    and None for "mehr als 6 h", which sets no limit.
    """
    if match := _ANSWER_RANGE.search(label):
        return max(_number(match.group(1)), _number(match.group(2)))
    if match := _ANSWER_UNDER.search(label):
        return _number(match.group(1))
    return None


def weekly_hours(rides: list[tuple[str, int]], today: date) -> float | None:
    """Average hours a week over the window, or None while it says too little.

    ``rides`` are ``(activity_date, duration_seconds)``. The weeks are counted
    from the first ride in the window, so a new athlete's two weeks are not
    spread over four.
    """
    start = today - timedelta(days=WINDOW_DAYS - 1)
    inside = []
    for day, seconds in rides:
        try:
            when = date.fromisoformat(str(day)[:10])
        except ValueError:
            continue
        if start <= when <= today:
            inside.append((when, seconds or 0))
    if len(inside) < MIN_ACTIVITIES:
        return None
    span = (today - min(when for when, _ in inside)).days + 1
    if span < MIN_SPAN_DAYS:
        return None
    return sum(seconds for _, seconds in inside) / 3600 / (span / 7)


def _hours_text(value: float, german: bool) -> str:
    # To the half hour, halves up: ``round`` would make 1.25 one hour.
    rounded = math.floor(value * 2 + 0.5) / 2
    text = f"{rounded:g}"
    return text.replace(".", ",") if german else text


def _range_text(low: float, high: float, german: bool) -> str:
    if high == float("inf"):
        return f"mehr als {_hours_text(low, True)}" if german else f"over {_hours_text(low, False)}"
    if low == 0:
        return f"unter {_hours_text(high, True)}" if german else f"under {_hours_text(high, False)}"
    return f"{_hours_text(low, german)}–{_hours_text(high, german)}"


def coach_note(actual: float, stated: tuple[float, float], german: bool) -> str:
    """One short note on what the rides showed (#159)."""
    if german:
        return (
            f"Deine ersten Fahrten zeigen etwas anderes als meine Annahme: Du trainierst "
            f"bisher etwa {_hours_text(actual, True)} Stunden pro Woche, ich bin von "
            f"{_range_text(*stated, True)} Stunden ausgegangen. Ab jetzt rechne ich damit. "
            "Auf dem Dashboard kannst du deinen Plan darauf neu bauen."
        )
    return (
        f"Your first rides tell a different story from my assumption: you train about "
        f"{_hours_text(actual, False)} hours a week so far, and I had assumed "
        f"{_range_text(*stated, False)}. I'm working from that now. You can rebuild your "
        "plan on it from the dashboard."
    )


async def check_starting_hypotheses(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Test the open weekly-volume assumption against the rides. Returns 1 when
    the rides settled or strengthened it, else 0."""
    hypothesis = await db.scalar(
        select(models.AthleteHypothesis).where(
            models.AthleteHypothesis.user_id == user.id,
            models.AthleteHypothesis.category == VOLUME_CATEGORY,
            models.AthleteHypothesis.status == "proposed",
        )
    )
    if hypothesis is None:
        return 0
    stated = stated_range(hypothesis.statement)
    if stated is None:
        return 0
    today = app_today(now, timezone_name or user.timezone_name)
    rows = await db.execute(
        select(models.RideMetric.activity_date, models.RideMetric.duration_seconds).where(
            models.RideMetric.user_id == user.id,
            models.RideMetric.activity_date >= (today - timedelta(days=WINDOW_DAYS - 1)).isoformat(),
        )
    )
    actual = weekly_hours([(day, seconds) for day, seconds in rows.all()], today)
    if actual is None:
        return 0

    low, high = stated
    if low * (1 - MARGIN) <= actual <= high * (1 + MARGIN):
        if low <= actual <= high and hypothesis.confidence < FITTING_CONFIDENCE:
            await crud.update_athlete_hypothesis(
                db, user.id, hypothesis.id, confidence=FITTING_CONFIDENCE
            )
            return 1
        return 0

    await crud.update_athlete_hypothesis(db, user.id, hypothesis.id, status="refuted")
    question = await db.scalar(
        select(models.AthleteInquiry).where(
            models.AthleteInquiry.user_id == user.id,
            models.AthleteInquiry.hypothesis_id == hypothesis.id,
        )
    )
    # The athlete's own words first; a new athlete may have said nothing yet,
    # and then the question was asked in their interface language.
    samples = await crud.get_recent_athlete_messages(db, user.id)
    language = athlete_language(samples[-1], samples[:-1]) if samples else None
    if language is None and question is not None:
        language = athlete_language(question.question)
    german = language == "German"
    if question is not None and question.status == "pending":
        # The rides answered it; asking now would ask about the past.
        await crud.resolve_athlete_inquiry(
            db, question, answer=f"Rides: about {_hours_text(actual, False)} h a week", now=now
        )
    await crud.create_chat_message(
        db,
        user.id,
        role="assistant",
        content=coach_note(actual, stated, german),
        timestamp=(now or datetime.now(timezone.utc)).isoformat(),
    )
    return 1
