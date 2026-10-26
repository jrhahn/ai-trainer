"""The plan in the language the athlete reads the app in (ai-trainer-ops#121).

Plan days are written and stored in English, and stay so: ``ride_matching`` and
the plan checks read English words in titles and descriptions. The owner's
choice (2026-10-18) was a display text next to them. The athlete's interface
language decides, and only German is translated so far.

Translations are keyed by the English text, not by the day. The web app sends
the whole plan back on every edit, so anything stored in a day would come back
from the client; and a day rewritten in English has no translation until one is
written, so a stale German text can never be shown for it.

The coach's own texts the athlete reads go the same way (ai-trainer-ops#215):
hypotheses, open questions, experiments and learned traits are stored in
English because the engine reads them.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta

import crud
import models
import schemas
from services import (
    ai_service,
    illness_return,
    rest_days,
    starting_hypotheses,
    workout_curiosity,
)
from services.llm import resolve_user_provider
from services.prompts import PLAN_TEXT_MAX_CHARS
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# The interface languages a plan is translated into, by the name the
# ``X-App-Language`` header resolves to, with the code they are stored under.
LANGUAGE_CODES = {"German": "de"}

# Texts the app writes itself, translated once by hand rather than by the model.
FIXED = {
    "de": {
        **starting_hypotheses.GERMAN,
        **rest_days.GERMAN,
        **illness_return.GERMAN,
        **workout_curiosity.GERMAN,
    }
}

# The days whose texts are translated: the ones the dashboard, the week strip
# and the calendar's current weeks show. Older days stay English.
PAST_DAYS = 14

# The most texts one call translates. A fortnight of days with descriptions,
# purposes and focus points fits; the rest is asked for on the next call.
MAX_TEXTS_PER_CALL = 60


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def plan_texts(plan: list, today: date) -> list[str]:
    """The texts the athlete reads in the plan's current days, each once, in plan order.

    Title, description, purpose, focus points and exercise names. The coach's
    feedback on a session is left out: it is written in the athlete's language
    already.
    """
    first = (today - timedelta(days=PAST_DAYS)).isoformat()
    texts: dict[str, None] = {}
    for day in plan:
        if not isinstance(day, dict) or str(day.get("date") or "") < first:
            continue
        found = _strings(
            [
                day.get("title"),
                day.get("description"),
                day.get("workoutPurpose"),
                day.get("keyFocusPoints"),
            ]
        )
        for exercise in day.get("strengthExercises") or []:
            if isinstance(exercise, dict):
                found += _strings(exercise.get("exercise"))
        for text in found:
            if len(text) <= PLAN_TEXT_MAX_CHARS:
                texts.setdefault(text)
    return list(texts)


async def stored_translations(
    db, user_id: str, code: str, texts: list[str]
) -> dict[str, str]:
    """The stored translations of ``texts``, keyed by the English text."""
    by_hash = {source_hash(text): text for text in texts}
    stored = await crud.get_plan_text_translations(db, user_id, code, list(by_hash))
    return {by_hash[digest]: text for digest, text in stored.items()}


async def coach_texts(db, user_id: str) -> list[str]:
    """The coach's texts the athlete reads: the open hypotheses and their
    rationale as the API shows it, the open questions with what they rest on
    and need, the suggested experiments and the learned traits with a
    contradiction noted on them.

    ponytail: the page asks for translations when a plan text changes, so a
    text written while the app is open stays English until then or a reload.
    """
    found = []
    for hypothesis in await crud.list_athlete_hypotheses(db, user_id):
        found += [
            hypothesis.statement,
            schemas.without_issue_reference(hypothesis.rationale or ""),
        ]
    for question in await crud.list_athlete_open_questions(db, user_id):
        found += [question.question, question.evidence, question.needs]
    for experiment in await crud.list_athlete_experiments(db, user_id):
        found += [experiment.question, experiment.protocol, experiment.rationale]
    found += await crud.visible_athlete_memory_fact_texts(db, user_id)
    return [text for text in _strings(found) if len(text) <= PLAN_TEXT_MAX_CHARS]


async def current_translations(
    db, user_id: str, code: str, today: date
) -> tuple[dict[str, str], list[str]]:
    """The translated texts of the stored plan and of the coach's texts, and
    the texts still untranslated. No model call."""
    stored_plan = await crud.get_training_plan(db, user_id)
    texts = list(
        dict.fromkeys(
            plan_texts(stored_plan.plan if stored_plan is not None else [], today)
            + await coach_texts(db, user_id)
        )
    )
    fixed = FIXED.get(code, {})
    known = {
        **await stored_translations(db, user_id, code, texts),
        **{text: fixed[text] for text in texts if text in fixed},
    }
    return known, [text for text in texts if text not in known]


async def ensure_translations(
    db, user: models.User, language: str, today: date, *, source: str
) -> tuple[dict[str, str], bool]:
    """The translations of the stored plan's texts, writing the missing ones,
    and whether texts are still missing afterwards.

    Empty for a language that is not translated. One model call at most; the
    texts past :data:`MAX_TEXTS_PER_CALL`, and any the model left out, are still
    missing, and the page asks again.
    """
    code = LANGUAGE_CODES.get(language)
    if code is None:
        return {}, False
    known, missing = await current_translations(db, user.id, code, today)
    if not missing:
        return known, False
    async with track_llm_usage(db, user, source=source):
        written = await ai_service.translate_plan_texts(
            missing[:MAX_TEXTS_PER_CALL], language, provider=resolve_user_provider(user)
        )
    await crud.save_plan_text_translations(
        db, user.id, code, {source_hash(text): written[text] for text in written}
    )
    return {**known, **written}, len(written) < len(missing)


async def translate_new_plan(
    db, user: models.User, language: str, today: date, *, source: str
) -> None:
    """Write the missing translations of a plan just written while the athlete
    waits for it, so that it comes up in their language at once rather than
    in English until the page asks (ai-trainer-ops#271).

    Best-effort: the plan is what was asked for. A failed call leaves the texts
    missing, and the page asks for them as before.
    """
    try:
        await ensure_translations(db, user, language, today, source=source)
    except Exception:
        logger.warning("Translating the new plan's texts failed", exc_info=True)
