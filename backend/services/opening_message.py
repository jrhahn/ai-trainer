"""The coach speaks first once onboarding is done (ai-trainer-ops#153).

In the owner's own walkthrough a new account got its first plan, which opened
with a rest day, and then nothing: the chat greeting is fixed frontend text,
and every way the coach asks something waits for eight activities. This writes
one coach message into the chat when onboarding finishes. It says what the plan
rests on, why it starts gently, and what the athlete can simply tell the coach.

Exactly once per account: ``users.opening_message_at`` is claimed with a
conditional update when the message is written, so a second ``isOnboarded``
write cannot post a second one. The text is composed before the request writes
anything, so the model call holds no lock. The model call is best-effort. When
it fails, times out or the budget is spent, a fixed text in the athlete's
language goes out instead, so the message is never missing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import ai_service, starting_hypotheses
from services.llm import resolve_user_provider
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# The athlete is waiting on the last onboarding step while this runs; past this
# the fixed text goes out instead.
MODEL_TIMEOUT_SECONDS = 20.0
# A message longer than this is not the short note the prompt asked for.
MAX_MESSAGE_CHARS = 1500
# How far ahead the message looks at the plan: the first week, which is the
# one the cold-start rule keeps moderate.
FIRST_DAYS = 7

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAYS_DE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Three things an athlete can simply say. English here; the model translates
# them, the fixed text has its own German ones.
EXAMPLES = (
    "I have no time on Saturday.",
    "I'm tired today, can we go easier?",
    "I have a race on 12 May.",
)
_EXAMPLES_DE = (
    "Samstag habe ich keine Zeit.",
    "Ich bin heute müde, können wir es lockerer machen?",
    "Am 12. Mai habe ich ein Rennen.",
)


def _sport_word(sports: list[str] | None) -> str:
    chosen = {str(s).strip().lower() for s in sports or [] if str(s).strip()}
    if chosen == {"running"}:
        return "running"
    if len(chosen) > 1:
        return "multisport endurance"
    return "cycling"


def _is_rest(day: dict) -> bool:
    return str(day.get("workoutType") or day.get("workout_type") or "rest").lower() == "rest"


def first_days(plan: list | None, today: date) -> list[dict]:
    """The plan's sessions from today on, for the first week, in date order."""
    end = today + timedelta(days=FIRST_DAYS)
    days: list[dict] = []
    for day in plan or []:
        if not isinstance(day, dict):
            continue
        try:
            when = date.fromisoformat(str(day.get("date"))[:10])
        except ValueError:
            continue
        if today <= when < end:
            days.append(
                {
                    "date": when.isoformat(),
                    "weekday": _WEEKDAYS[when.weekday()],
                    "workoutType": day.get("workoutType") or day.get("workout_type") or "rest",
                    "title": str(day.get("title") or "")[:80],
                    "durationMinutes": day.get("durationMinutes") or day.get("duration_minutes") or 0,
                }
            )
    return sorted(days, key=lambda d: d["date"])


def build_facts(user: models.User, plan: list | None, today: date) -> dict:
    """What the message may say: the onboarding answers and the first week."""
    answers: dict = {"sports": list(user.sports or [])}
    if user.training_goal == "race" or user.race_date:
        answers["goal"] = "race"
        if user.race_date:
            answers["raceDate"] = user.race_date
        if user.race_description:
            answers["race"] = user.race_description
    else:
        answers["goal"] = "general fitness"
    if user.training_days:
        answers["trainingDays"] = list(user.training_days)
    if user.weekly_hours:
        answers["weeklyHours"] = user.weekly_hours
    if user.fitness_level:
        answers["level"] = user.fitness_level
    if user.current_ftp:
        answers["ftpWatts"] = user.current_ftp
    days = first_days(plan, today)
    today_sessions = [d for d in days if d["date"] == today.isoformat()]
    return {
        "answers": answers,
        "today": today.isoformat(),
        "firstWeek": days,
        "first_day_is_rest": bool(today_sessions) and all(_is_rest(d) for d in today_sessions),
        "examples": list(EXAMPLES),
    }


def fixed_message(facts: dict, language: str | None) -> str:
    """The message without a model: same three parts, in German or English."""
    answers = facts.get("answers") or {}
    german = language == "German"
    days = answers.get("trainingDays") or []
    if german:
        built = ["deinen Antworten aus der Anmeldung"]
        if days:
            built.append("deinen Trainingstagen")
        if answers.get("goal") == "race":
            built.append("deinem Rennen")
        lines = [
            "Hallo! Dein erster Plan steht. Er beruht auf " + " und ".join(built) + ".",
            "",
            "Ich kenne noch keine einzige Fahrt von dir, deshalb starten wir die erste "
            "Woche bewusst locker: Die ersten Einheiten messen erst einmal, wo du stehst.",
        ]
        if facts.get("first_day_is_rest"):
            lines.append(
                "Heute ist deshalb ein Ruhetag. Wenn du lieber gleich loslegen willst, "
                "sag es mir einfach."
            )
        lines += [
            "",
            "Je mehr ich über dich weiß, desto besser wird der Plan. Schreib mir hier "
            "einfach, zum Beispiel:",
            *[f"- „{e}“" for e in _EXAMPLES_DE],
        ]
        if facts.get("asks"):
            lines += [
                "",
                "Zwei Annahmen über dich möchte ich prüfen. Die Fragen dazu sind oben "
                "im Chat angeheftet, eine Antwort ist ein Tipp.",
            ]
        return "\n".join(lines)
    built = ["your answers from sign-up"]
    if days:
        built.append("your training days")
    if answers.get("goal") == "race":
        built.append("your race")
    lines = [
        "Hi! Your first plan is ready. It is built from " + " and ".join(built) + ".",
        "",
        "I have not seen a single ride of yours yet, so the first week starts "
        "deliberately easy: the first sessions measure where you are.",
    ]
    if facts.get("first_day_is_rest"):
        lines.append(
            "That is why today is a rest day. If you would rather start right away, "
            "just tell me."
        )
    lines += [
        "",
        "The more I know about you, the better the plan gets. Just write to me here, "
        "for example:",
        *[f"- “{e}”" for e in EXAMPLES],
    ]
    if facts.get("asks"):
        lines += [
            "",
            "There are two assumptions about you I'd like to check. The questions are "
            "pinned above the chat, and an answer takes one tap.",
        ]
    return "\n".join(lines)


@dataclass(frozen=True)
class OpeningMessage:
    """What the coach says first, and the assumptions it asks about (#154)."""

    content: str
    hypotheses: list[starting_hypotheses.StartingHypothesis]
    language: str | None


async def _model_message(
    db: AsyncSession, user: models.User, facts: dict, language: str
) -> tuple[str, list]:
    async with track_llm_usage(db, user, source="step:opening-message"):
        return await asyncio.wait_for(
            ai_service.write_opening_message(
                facts,
                language,
                _sport_word(user.sports),
                provider=resolve_user_provider(user),
            ),
            timeout=MODEL_TIMEOUT_SECONDS,
        )


async def compose_opening_message(
    db: AsyncSession,
    user: models.User,
    *,
    language: str | None,
    today: date,
) -> OpeningMessage:
    """The coach's first message and its starting assumptions. Writes nothing.

    Called before the request writes anything, so the model call (up to
    ``MODEL_TIMEOUT_SECONDS``) holds no lock on the account or the database.
    ``language`` is "German", "English" or None (English).
    """
    try:
        # In a savepoint: a failed read would otherwise leave a Postgres
        # transaction aborted, and the onboarding write after it would fail.
        async with db.begin_nested():
            plan_row = await crud.get_training_plan(db, user.id)
        facts = build_facts(user, plan_row.plan if plan_row else None, today)
    except Exception:
        # Without the plan the message still says the three things, minus
        # today's rest day; it is never worth failing onboarding over.
        logger.warning("Opening message without the plan for %s", user.id, exc_info=True)
        facts = build_facts(user, None, today)
    content, raw = "", []
    try:
        content, raw = await _model_message(db, user, facts, language or "English")
    except Exception:
        # A spent budget, a rate limit, a timeout or a broken reply: the fixed
        # text says the same three things.
        logger.warning("Opening message fell back to the fixed text for %s", user.id, exc_info=True)
    hypotheses = starting_hypotheses.parse(raw)
    if not content or len(content) > MAX_MESSAGE_CHARS or not hypotheses:
        # The model's message names the model's assumptions; with the fixed
        # assumptions it has to be the fixed text, which names them generically.
        hypotheses = starting_hypotheses.fallback(facts.get("answers") or {}, language)
        content = fixed_message({**facts, "asks": True}, language)
    return OpeningMessage(content, hypotheses, language)


async def post_opening_message(
    db: AsyncSession, user: models.User, opening: OpeningMessage
) -> models.ChatMessage | None:
    """Claim the account's one opening message and write it. Never raises.

    The claim is a conditional update, so two onboarding writes racing each
    other post one message between them. Claim and message share a savepoint:
    if the write fails, neither is kept, and onboarding still finishes.
    """
    # Read now: a rolled-back savepoint expires the row, and reading an
    # expired attribute here would need IO the except branch cannot do.
    user_id = user.id
    try:
        async with db.begin_nested():
            claimed = await db.execute(
                update(models.User)
                .where(models.User.id == user_id, models.User.opening_message_at.is_(None))
                .values(opening_message_at=datetime.now(timezone.utc))
            )
            if claimed.rowcount != 1:
                return None
            now = datetime.now(timezone.utc)
            message = await crud.create_chat_message(
                db,
                user_id,
                role="assistant",
                content=opening.content,
                timestamp=now.isoformat(),
            )
    except Exception:
        logger.warning("Could not write the opening message for %s", user_id, exc_info=True)
        return None
    # Its own savepoint: the message stands even when the assumptions cannot be
    # stored (ai-trainer-ops#154).
    try:
        async with db.begin_nested():
            await starting_hypotheses.store(
                db, user_id, opening.hypotheses, language=opening.language, now=now
            )
    except Exception:
        logger.warning("Could not store the starting hypotheses for %s", user_id, exc_info=True)
    return message
