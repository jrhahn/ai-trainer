"""What the coach assumes about a new athlete, and asks to check (ai-trainer-ops#154).

Every way the coach learns about an athlete waits for eight activities: the
hypotheses, the inquiries, the open questions. A new athlete therefore gets no
question in exactly the weeks when one helps most. The owner, after their own
walkthrough: the coach should start with a few hypotheses and try to answer
them from data or by asking directly.

So the opening message (#153) states two starting assumptions, each with one
question the athlete can answer with a tap. Each assumption is stored as an
ordinary hypothesis, so the rides and the weekly engine take it from there; the
question is an ordinary pinned inquiry whose answers say whether they confirm
or refute it. The model proposes them from the onboarding answers. When it
proposes nothing usable, two fixed ones go out instead, so a new athlete always
has something to answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

import crud
from services import untrusted_text
from services.uncertainty_lifecycle import is_falsifiable_statement

logger = logging.getLogger(__name__)

# What a starting assumption may be about. Fixed, so the first rides can test
# each one deterministically later (#159).
CATEGORIES = (
    "weekly_volume",
    "threshold_power",
    "recovery",
    "experience",
    "goal_detail",
)
# A pinned question is the most expensive thing the coach can spend: two at most,
# the pending limit the regular inquiries keep too.
MAX_STARTING = 2
STANCES = ("confirms", "refutes", "neutral")
MAX_QUESTION_CHARS = 200
MAX_OPTION_CHARS = 40
MIN_OPTIONS, MAX_OPTIONS = 2, 4
# Low: an assumption from four onboarding answers, not an observation.
STARTING_CONFIDENCE = 0.3
CATEGORY_PREFIX = "starting_"

_RATIONALE = (
    "Starting assumption from the onboarding answers; the athlete's answer and "
    "the first rides test it."
)
# The fixed texts as a German athlete reads them (ai-trainer-ops#215). The
# English ones stay what is stored: the checks and the dedupe read English.
GERMAN = {
    "Has ridden under 3 hours a week over the last two months": (
        "Du bist in den letzten zwei Monaten unter 3 Stunden pro Woche gefahren"
    ),
    "Has ridden 3 to 6 hours a week over the last two months": (
        "Du bist in den letzten zwei Monaten 3 bis 6 Stunden pro Woche gefahren"
    ),
    "Has ridden over 6 hours a week over the last two months": (
        "Du bist in den letzten zwei Monaten mehr als 6 Stunden pro Woche gefahren"
    ),
    "Has not trained to a structured plan before": (
        "Du hast noch nie nach einem strukturierten Plan trainiert"
    ),
    "Has trained to a structured plan with intervals before": (
        "Du hast schon nach einem strukturierten Plan mit Intervallen trainiert"
    ),
    _RATIONALE: (
        "Eine Annahme aus deinen Antworten beim Start. Deine Antwort und die "
        "ersten Fahrten prüfen sie."
    ),
}
_WHY = {
    "German": "Damit dein Plan von Anfang an zu dir passt.",
    "English": "So your plan fits you from the start.",
}


@dataclass(frozen=True)
class StartingHypothesis:
    category: str
    statement: str
    question: str
    options: list[dict] = field(default_factory=list)


def _clean(value: object, limit: int) -> str:
    text = " ".join(untrusted_text.unmark(str(value or "")).split())
    return text if 0 < len(text) <= limit else ""


def parse(raw: object) -> list[StartingHypothesis]:
    """The usable starting hypotheses in a model reply, at most ``MAX_STARTING``.

    One per category. A hypothesis is dropped whole when any part does not fit:
    a statement too long to ever recur, a question too long to read on a phone,
    or answers that are too few, too many, too long, or none of which confirms
    or refutes anything.
    """
    found: list[StartingHypothesis] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip().lower()
        statement = _clean(item.get("statement"), 400)
        question = _clean(item.get("question"), MAX_QUESTION_CHARS)
        options = []
        for option in item.get("options") or []:
            if not isinstance(option, dict):
                continue
            label = _clean(option.get("label"), MAX_OPTION_CHARS)
            stance = str(option.get("stance") or "").strip().lower()
            if label and stance in STANCES:
                options.append({"label": label, "stance": stance})
        if (
            category not in CATEGORIES
            or category in seen
            or not statement
            or not is_falsifiable_statement(statement)
            or not question
            or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS
            or len({o["label"].casefold() for o in options}) != len(options)
            or all(o["stance"] == "neutral" for o in options)
        ):
            continue
        seen.add(category)
        found.append(StartingHypothesis(category, statement, question, options))
        if len(found) >= MAX_STARTING:
            break
    return found


def fallback(answers: dict, language: str | None) -> list[StartingHypothesis]:
    """Two fixed starting hypotheses, worded for the stated level."""
    german = language == "German"
    level = str(answers.get("level") or "").lower()
    hours = {"beginner": "under 3", "advanced": "over 6"}.get(level, "3 to 6")
    low, mid, high = (
        ("unter 3 h", "3–6 h", "mehr als 6 h")
        if german
        else ("under 3 h", "3–6 h", "over 6 h")
    )
    expected = {"under 3": low, "over 6": high}.get(hours, mid)
    volume = StartingHypothesis(
        category="weekly_volume",
        statement=f"Has ridden {hours} hours a week over the last two months",
        question=(
            "Wie viele Stunden pro Woche hast du in den letzten zwei Monaten im Schnitt trainiert?"
            if german
            else "How many hours a week did you train on average over the last two months?"
        ),
        options=[
            {"label": label, "stance": "confirms" if label == expected else "refutes"}
            for label in (low, mid, high)
        ],
    )
    beginner = level == "beginner"
    experience = StartingHypothesis(
        category="experience",
        statement=(
            "Has not trained to a structured plan before"
            if beginner
            else "Has trained to a structured plan with intervals before"
        ),
        question=(
            "Hast du schon einmal nach einem Plan mit Intervallen trainiert?"
            if german
            else "Have you trained to a plan with intervals before?"
        ),
        options=[
            {
                "label": "Ja, regelmäßig" if german else "Yes, regularly",
                "stance": "refutes" if beginner else "confirms",
            },
            {"label": "Ab und zu" if german else "Now and then", "stance": "neutral"},
            {
                "label": "Nein, noch nie" if german else "No, never",
                "stance": "confirms" if beginner else "refutes",
            },
        ],
    )
    return [volume, experience]


async def store(
    db: AsyncSession,
    user_id: str,
    hypotheses: list[StartingHypothesis],
    *,
    language: str | None,
    now: datetime,
) -> int:
    """Store each hypothesis and pin its question. Returns the questions pinned.

    A hypothesis the channel has no room for gets no question: a question about
    an assumption that was never recorded would have nothing to confirm.
    """
    pinned = 0
    for index, hypothesis in enumerate(hypotheses[:MAX_STARTING]):
        # A microsecond apart, so the questions are pinned in the coach's order:
        # the list sorts by when a question was asked.
        asked = now + timedelta(microseconds=index)
        category = CATEGORY_PREFIX + hypothesis.category
        row = await crud.propose_athlete_hypothesis(
            db,
            user_id,
            statement=hypothesis.statement,
            category=category,
            rationale=_RATIONALE,
            confidence=STARTING_CONFIDENCE,
            observed_at=asked,
        )
        if row is None:
            continue
        inquiry = await crud.record_athlete_inquiry(
            db,
            user_id,
            question=hypothesis.question,
            category=category,
            why_asking=_WHY["German" if language == "German" else "English"],
            observed_at=asked,
            options=hypothesis.options,
            hypothesis_id=row.id,
        )
        if inquiry is not None:
            pinned += 1
    return pinned
