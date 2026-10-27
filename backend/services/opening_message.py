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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services import ai_service, plan_translations, prompts, starting_hypotheses
from services.llm import resolve_user_provider
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# The athlete is waiting on the last onboarding step while this runs; past this
# the fixed text goes out instead.
MODEL_TIMEOUT_SECONDS = 20.0
# The assumptions' German is one more call on the same wait, so the worst case
# is the two limits added, about half a minute; past this one the fixed
# assumptions, whose German is written by hand, go out instead (#230).
TRANSLATION_TIMEOUT_SECONDS = 10.0
# The model's three parts longer than this are not the short note the prompt
# asked for. The stored message is longer by the assumptions the app lists
# under them (ai-trainer-ops#230).
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


_WEEKDAY_KEYS = tuple(day[:3] for day in schemas.WEEK_ORDER)


def _is_rest(day: dict) -> bool:
    return (
        str(day.get("workoutType") or day.get("workout_type") or "rest").lower()
        == "rest"
    )


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
                    "workoutType": day.get("workoutType")
                    or day.get("workout_type")
                    or "rest",
                    "title": str(day.get("title") or "")[:80],
                    "durationMinutes": day.get("durationMinutes")
                    or day.get("duration_minutes")
                    or 0,
                }
            )
    return sorted(days, key=lambda d: d["date"])


# The interface's own words for what was stored (ai-trainer-ops#190). The
# model got "cycling", "race", "tuesday" and "advanced" and wrote them into a
# German message; Werner's "Erfahren" came back as "fortgeschritten".
_SHOWN = {
    "German": {
        "cycling": "Radfahren",
        "running": "Laufen",
        "strength": "Kraft",
        "race": "Rennvorbereitung",
        "general fitness": "Allgemeine Fitness",
        "beginner": "Einstieg",
        "intermediate": "Fortgeschritten",
        "advanced": "Erfahren",
        "monday": "Montag",
        "tuesday": "Dienstag",
        "wednesday": "Mittwoch",
        "thursday": "Donnerstag",
        "friday": "Freitag",
        "saturday": "Samstag",
        "sunday": "Sonntag",
    },
    "English": {
        "cycling": "Cycling",
        "running": "Running",
        "strength": "Strength",
        "race": "Race Prep",
        "general fitness": "General Fitness",
        "beginner": "Beginner",
        "intermediate": "Intermediate",
        "advanced": "Advanced",
        **{day: day.capitalize() for day in schemas.WEEK_ORDER},
    },
}


_MONTHS = {
    "German": (
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ),
    "English": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}


def spoken_date(value: object, language: str | None) -> object:
    """``2027-04-26`` as the language writes it: "26. April 2027", "26 April 2027".

    The model copied the race date into the opening message as the facts carried
    it, 2027-04-26 (ai-trainer-ops#308). Another language, or a value that is no
    date, stays as it is.
    """
    months = _MONTHS.get(language or "English")
    if months is None:
        return value
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return value
    separator = ". " if language == "German" else " "
    return f"{day.day}{separator}{months[day.month - 1]} {day.year}"


def shown_answers(answers: dict, language: str | None) -> dict:
    """``answers`` in the words the athlete picked them in, days Monday first."""
    words = _SHOWN.get(language or "English", _SHOWN["English"])

    def say(value: object) -> object:
        return words.get(value, value) if isinstance(value, str) else value

    shown = dict(answers)
    for key in ("goal", "level"):
        if key in shown:
            shown[key] = say(shown[key])
    if "sports" in shown:
        shown["sports"] = [say(s) for s in shown["sports"]]
    if "trainingDays" in shown:
        shown["trainingDays"] = [
            say(d) for d in schemas.week_ordered(shown["trainingDays"])
        ]
    if "raceDate" in shown:
        shown["raceDate"] = spoken_date(shown["raceDate"], language)
    return shown


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
    first_day_is_rest = bool(today_sessions) and all(
        _is_rest(d) for d in today_sessions
    )
    # Which reason holds, so the model states it rather than picks one: Sam
    # signed up on a training day and was told it was not one (ai-trainer-ops#230).
    training_days = {str(d).lower()[:3] for d in user.training_days or []}
    off_day = (
        bool(training_days) and _WEEKDAY_KEYS[today.weekday()] not in training_days
    )
    return {
        "answers": answers,
        "today": today.isoformat(),
        "firstWeek": days,
        "first_day_is_rest": first_day_is_rest,
        "first_day_reason": (
            ("not_a_training_day" if off_day else "eases_in")
            if first_day_is_rest
            else None
        ),
        "examples": list(EXAMPLES),
    }


# The lead above the assumptions the app lists under the message (#230).
_ASSUMPTIONS_LEAD = {
    "German": "Zwei Annahmen über dich möchte ich prüfen",
    "English": "There are two assumptions about you I'd like to check",
}


def assumptions_block(lines: list[str], language: str | None) -> str:
    """The starting assumptions under the message, as the hypothesis cards show
    them, and the line on the pinned questions (ai-trainer-ops#230).

    The model used to translate the English statements into its message on the
    fly, and garbled them ("Einvoraus-Ermüdung"). The app lists them itself:
    in English as stored, in German as the plan-text translation has them.
    Without lines it names them generically, as the fixed text always did.
    """
    key = "German" if language == "German" else "English"
    if not lines:
        return f"{_ASSUMPTIONS_LEAD[key]}. {prompts.PINNED_QUESTIONS_LINE[key]}"
    return "\n".join(
        [
            f"{_ASSUMPTIONS_LEAD[key]}:",
            *[f"- {line}" for line in lines],
            "",
            prompts.PINNED_QUESTIONS_LINE[key],
        ]
    )


def fixed_message(facts: dict, language: str | None) -> str:
    """The message without a model: same three parts, in German or English. The
    assumptions are listed under it as under the model's (ai-trainer-ops#230)."""
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
            # The reason the facts give, as the model is told to (#230): "deshalb"
            # told an athlete on a day off that the plan was easing in.
            lines.append(
                (
                    "Heute ist keiner deiner Trainingstage, also ein Ruhetag. "
                    if facts.get("first_day_reason") == "not_a_training_day"
                    else "Heute ist deshalb ein Ruhetag. "
                )
                + "Wenn du lieber gleich loslegen willst, sag es mir einfach."
            )
        lines += [
            "",
            "Je mehr ich über dich weiß, desto besser wird der Plan. Schreib mir hier "
            "einfach, zum Beispiel:",
            *[f"- „{e}“" for e in _EXAMPLES_DE],
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
            (
                "Today is not one of your training days, so it is a rest day. "
                if facts.get("first_day_reason") == "not_a_training_day"
                else "That is why today is a rest day. "
            )
            + "If you would rather start right away, just tell me."
        )
    lines += [
        "",
        "The more I know about you, the better the plan gets. Just write to me here, "
        "for example:",
        *[f"- “{e}”" for e in EXAMPLES],
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class OpeningMessage:
    """What the coach says first, and the assumptions it asks about (#154)."""

    content: str
    hypotheses: list[starting_hypotheses.StartingHypothesis]
    language: str | None
    # The assumptions' German that the message shows, keyed by the English
    # statement, kept as the plan-text translations so that the hypothesis
    # cards read the same words (ai-trainer-ops#230).
    translations: dict[str, str] = field(default_factory=dict)


async def _shown_statements(
    db: AsyncSession,
    user: models.User,
    hypotheses: list[starting_hypotheses.StartingHypothesis],
    language: str | None,
) -> tuple[list[str], dict[str, str]] | None:
    """The statements as the athlete reads them, and the translations written
    for them; ``None`` when a German one could not be written.

    English as stored. German from the hand-written fixed texts, else from one
    call to the plan-text translation, the step every coach text passes before
    the athlete reads it (ai-trainer-ops#258). A model's on-the-fly German in
    the message is what this replaces, so a statement the translation leaves
    out is never shown in another way.
    """
    statements = [h.statement for h in hypotheses]
    code = plan_translations.LANGUAGE_CODES.get(language or "")
    if code is None:
        return statements, {}
    fixed = plan_translations.FIXED.get(code, {})
    missing = [text for text in statements if text not in fixed]
    written: dict[str, str] = {}
    if missing:
        try:
            async with track_llm_usage(db, user, source="step:opening-message"):
                written = await asyncio.wait_for(
                    ai_service.translate_plan_texts(
                        missing, language, provider=resolve_user_provider(user)
                    ),
                    timeout=TRANSLATION_TIMEOUT_SECONDS,
                )
        except Exception:
            logger.warning(
                "The starting assumptions could not be translated for %s",
                user.id,
                exc_info=True,
            )
            return None
    german = {**written, **{text: fixed[text] for text in statements if text in fixed}}
    if any(text not in german for text in statements):
        return None
    return [german[text] for text in statements], written


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
        logger.warning(
            "Opening message without the plan for %s", user.id, exc_info=True
        )
        facts = build_facts(user, None, today)
    content, raw = "", []
    try:
        content, raw = await _model_message(
            db,
            user,
            {**facts, "answers": shown_answers(facts.get("answers") or {}, language)},
            language or "English",
        )
    except Exception:
        # A spent budget, a rate limit, a timeout or a broken reply: the fixed
        # text says the same three things.
        logger.warning(
            "Opening message fell back to the fixed text for %s", user.id, exc_info=True
        )
    hypotheses = starting_hypotheses.parse(raw)
    if not content or len(content) > MAX_MESSAGE_CHARS:
        content = fixed_message(facts, language)
    if len(hypotheses) < starting_hypotheses.MAX_STARTING:
        # Two assumptions are listed under every message; with fewer usable ones,
        # or none, the fixed assumptions go out. The message stays the model's:
        # it names no assumption, since the app lists them under it. Replacing
        # it too cost about half the German athletes their first message, over
        # an answer label longer than 40 characters (real model, 2026-10-27).
        hypotheses = starting_hypotheses.fallback(facts.get("answers") or {}, language)
    shown = await _shown_statements(db, user, hypotheses, language)
    if shown is None:
        # Their German could not be written: the fixed assumptions, whose German
        # is written by hand, rather than English lines or a garbled guess.
        hypotheses = starting_hypotheses.fallback(facts.get("answers") or {}, language)
        shown = await _shown_statements(db, user, hypotheses, language) or ([], {})
    lines, translations = shown
    content = f"{content}\n\n{assumptions_block(lines, language)}"
    return OpeningMessage(content, hypotheses, language, translations)


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
                .where(
                    models.User.id == user_id, models.User.opening_message_at.is_(None)
                )
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
        logger.warning(
            "Could not write the opening message for %s", user_id, exc_info=True
        )
        return None
    # Its own savepoint: the message stands even when the assumptions cannot be
    # stored (ai-trainer-ops#154).
    try:
        async with db.begin_nested():
            await starting_hypotheses.store(
                db, user_id, opening.hypotheses, language=opening.language, now=now
            )
    except Exception:
        logger.warning(
            "Could not store the starting hypotheses for %s", user_id, exc_info=True
        )
    # The German the message shows, kept where the hypothesis cards read theirs,
    # so both say the same words and the page asks for no second translation
    # (ai-trainer-ops#230). Best-effort, in its own savepoint.
    code = plan_translations.LANGUAGE_CODES.get(opening.language or "")
    if code is not None and opening.translations:
        try:
            async with db.begin_nested():
                await crud.save_plan_text_translations(
                    db,
                    user_id,
                    code,
                    {
                        plan_translations.source_hash(text): german
                        for text, german in opening.translations.items()
                    },
                )
        except Exception:
            logger.warning(
                "Could not keep the assumptions' German for %s", user_id, exc_info=True
            )
    return message
