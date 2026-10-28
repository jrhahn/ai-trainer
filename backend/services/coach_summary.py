"""Narrate automated coach plan changes to the athlete (#439).

The plan pipeline records *what* an automated run changed (``PlanDayHistory``
rows sharing a ``batch_id``). This module adds the *why*: one LLM call turns a
run's applied changes into a first-person summary plus a per-day reason, then
persists them and posts a single coach chat message. It is deliberately
fail-safe — narration must never break the plan write that preceded it.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from domain.availability import WEEKDAYS
from domain.plan_day import planned_minutes
from services import ai_service
from services.dates import app_today
from services.llm import resolve_user_provider
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# Automated triggers whose plan changes earn a one-message narrative. User-driven
# triggers (``user_edit``, ``coach_chat``, ``next_ride``) already surface in the
# UI, and a full ``generate`` is an initial build rather than an adjustment to
# explain — see services/plan_pipeline.PLAN_SOURCES.
NARRATED_SOURCES = frozenset(
    {
        "nightly_maintenance",
        "adapt",
        "auto_adapt",
        "ride_review",
        "history_review",
        # Unlike a first "generate", a rebuild after a connect changes a plan the
        # athlete already has, and was asked by nobody (ai-trainer-ops#306).
        "history_rebuild",
    }
)

# How the coach frames each run when addressing the athlete.
_RUN_CONTEXT = {
    "nightly_maintenance": "your daily overnight routine check of the plan",
    "adapt": "an automatic refresh of your plan after it had gone stale",
    "auto_adapt": "an automatic adjustment after your latest activity synced",
    "ride_review": "a review of your plan after your most recent ride",
    "history_review": (
        "a look at the rides of the last weeks you just brought in from intervals.icu"
    ),
    "history_rebuild": (
        "a rebuild of your whole plan from the rides of the last weeks you just "
        "brought in from intervals.icu, so it is built from what you actually rode"
    ),
}


# A day the message calls free: "Donnerstag ist frei", "Ruhetag am Donnerstag",
# "Thursday is a rest day" (ai-trainer-ops#316). Narrow on purpose: "eine kurze
# Pause zwischen den Intervallen" or "the rest of the set" call no day free.
# "frei nach Gefühl" is freely, and a pause between intervals no day off.
_NOT_A_DAY = r"(?!\s+(?:nach|von|weg|raus|heraus|zwischen|between|after|of)\b)"
_FREE_AFTER = (
    r"(?:frei|trainingsfrei|ruhetage?|pause|free|rest\s+days?|days?\s+off)" + _NOT_A_DAY
)
_FREE_BEFORE = r"(?:frei|ruhetage?|pause|ruhe|rest\s+days?|rest|days?\s+off|break)"
_NEGATION = re.compile(r"\b(?:nicht|kein\w*|not|no)\b")
_DAY_WORD = r"(?:" + "|".join(WEEKDAYS) + r"|heute|today|morgen|tomorrow|übermorgen)"


def _day_date(word: str, today: date) -> str:
    """The date within the coming week that ``word`` names."""
    relative = {"heute": 0, "today": 0, "morgen": 1, "tomorrow": 1, "übermorgen": 2}
    if word in relative:
        return (today + timedelta(days=relative[word])).isoformat()
    return (today + timedelta(days=(WEEKDAYS[word] - today.weekday()) % 7)).isoformat()


# The days listed before the one a match names: "Donnerstag, Freitag und
# Samstag sind frei" frees all three (review of #1212).
_LISTED_BEFORE = re.compile(rf"(?:\b(?P<day>{_DAY_WORD})s?\s*(?:,|und|and|&)\s*)+$")
_LISTED_DAY = re.compile(rf"\b({_DAY_WORD})s?\b")


def days_called_free(text: str, today: date) -> list[str]:
    """The dates of the coming week that ``text`` says are free."""
    lowered = text.casefold()
    found: set[str] = set()
    after = re.compile(
        rf"\b(?P<day>{_DAY_WORD})s?\b[:\u2013\u2014-]?"
        rf"(?P<gap>(?:\s+[^\s.!?;]+){{0,3}}?)\s+{_FREE_AFTER}\b"
    )
    # From every day named, not from the last match on: a match over "Freitag
    # und Samstag sind frei" is dropped for the weekday in its gap, and must not
    # swallow the "Samstag sind frei" inside it.
    for mention in _LISTED_DAY.finditer(lowered):
        match = after.match(lowered, mention.start())
        if match is None:
            continue
        gap = match.group("gap")
        if _NEGATION.search(gap) or re.search(rf"\b{_DAY_WORD}s?\b", gap):
            continue
        found.add(_day_date(match.group("day"), today))
        listed = _LISTED_BEFORE.search(lowered[: match.start()])
        if listed:
            found.update(
                _day_date(day, today) for day in _LISTED_DAY.findall(listed.group())
            )
    before = re.compile(
        rf"\b{_FREE_BEFORE}\s+(?:am|an|on|für|for)\s+(?:den\s+|the\s+)?(?P<day>{_DAY_WORD})s?\b"
    )
    for match in before.finditer(lowered):
        if _NEGATION.search(lowered[max(0, match.start() - 12) : match.start()]):
            continue
        found.add(_day_date(match.group("day"), today))
    return sorted(found)


def _free_days_with_a_session(text: str, plan: list[dict], today: date) -> list[str]:
    """The days ``text`` calls free that hold a session in ``plan``."""
    trained = {
        str(d.get("date"))
        for d in plan
        if isinstance(d, dict) and planned_minutes(d) > 0
    }
    return [day for day in days_called_free(text, today) if day in trained]


async def narrate_plan_changes(
    db: AsyncSession,
    user: models.User,
    *,
    batch_id: str | None,
    source: str,
    applied_changes: list[dict],
    profile: dict | None = None,
    rider_assessment: dict | None = None,
    training_load_section: str = "",
    weather_context_section: str = "",
    reply_language: str | None = None,
    plan: list[dict],
) -> models.PlanChangeSummary | None:
    """Summarise one automated coach run and post a single coach chat message.

    No-op unless ``source`` is narrated, a ``batch_id`` was recorded, and at least
    one change was actually applied (a blocked-only run changed nothing the
    athlete should hear about). Persists a ``PlanChangeSummary``, backfills each
    changed day's ``reason``, and creates exactly one assistant ``ChatMessage``.
    Any failure is swallowed and logged. ``reply_language`` is the language to
    write in when the athlete has not chatted yet (ai-trainer-ops#306).

    ``plan`` is the plan as the run committed it, and required, so no writer
    can leave the check out by forgetting it. A message that calls a day
    free which still holds a session there is not posted: the run then shows
    as its timeline card, which lists what actually changed (ai-trainer-ops#316).
    """
    if source not in NARRATED_SOURCES or not batch_id or not applied_changes:
        return None
    try:
        if profile is None:
            profile = schemas.UserProfileSchema.from_user(user).model_dump(
                by_alias=True
            )
        # Only read the relationship if it is already loaded — touching an
        # unloaded relationship inside async SQLAlchemy raises. Callers that have
        # it eagerly loaded (nightly maintenance) get a personalised assessment;
        # others simply omit it.
        if (
            rider_assessment is None
            and "rider_assessment" not in sa_inspect(user).unloaded
            and user.rider_assessment is not None
        ):
            rider_assessment = schemas.RiderAssessmentSchema.model_validate(
                user.rider_assessment, from_attributes=True
            ).model_dump(by_alias=True)
        provider = resolve_user_provider(user)
        # Nobody prompted this message, so unlike the coach chat it has no athlete
        # turn to mirror. These few lines are the only thing telling it which
        # language the athlete speaks (#658).
        language_samples = await crud.get_recent_athlete_messages(db, user.id)
        async with track_llm_usage(db, user, source="step:coach-narration"):
            parsed = await ai_service.summarize_plan_changes(
                applied_changes,
                profile,
                run_context=_RUN_CONTEXT.get(source, "an automatic plan adjustment"),
                provider=provider,
                rider_assessment=rider_assessment,
                training_load_section=training_load_section,
                weather_context_section=weather_context_section,
                language_samples=language_samples,
                timezone_name=user.timezone_name,
                reply_language=reply_language,
            )

        summary = (parsed.get("summary") or "").strip()
        if not summary:
            logger.warning(
                "Coach narration produced no summary for batch_id=%s; skipping",
                batch_id,
            )
            return None

        today = app_today(timezone_name=getattr(user, "timezone_name", None))
        contradicted = _free_days_with_a_session(summary, plan, today)
        if contradicted:
            logger.warning(
                "Coach narration for batch_id=%s calls %s free, which the plan "
                "still trains; not posted",
                batch_id,
                ", ".join(contradicted),
            )
            return None

        # A reason only for a day this run changed: it is written onto the
        # batch's rows by date, blocked ones included (ai-trainer-ops#316).
        changed = {str(c.get("date")) for c in applied_changes}
        reasons = {
            entry["date"]: entry["reason"].strip()
            for entry in parsed.get("days") or []
            if isinstance(entry, dict)
            and entry.get("date") in changed
            and (entry.get("reason") or "").strip()
        }

        row = await crud.create_plan_change_summary(
            db, user.id, batch_id=batch_id, source=source, summary=summary
        )
        if reasons:
            await crud.set_plan_day_reasons(db, batch_id, reasons)
        await crud.create_chat_message(
            db,
            user.id,
            role="assistant",
            content=summary,
            timestamp=datetime.now(timezone.utc).isoformat(),
            plan_update_count=len(applied_changes),
        )
        return row
    except Exception:
        logger.warning(
            "Coach narration failed for user_id=%s batch_id=%s",
            user.id,
            batch_id,
            exc_info=True,
        )
        return None
