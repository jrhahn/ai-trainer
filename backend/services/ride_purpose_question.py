"""The question the coach asks about a session it could not classify (#580).

The coach already asked it — as a sentence inside the login summary, rendered as
body text in a card. There was no answer field, no state and no consequence: the
athlete could not reply anywhere, and the coach never learned the answer. A
question the coach says it needs an answer to in order to work should be an
object with a lifecycle, not prose in a paragraph.

The answer belongs on the *session*, not on the athlete. "Thursday was 4×8 at
threshold" is a fact about one activity; routing it through the memory-fact
channel that :class:`models.AthleteInquiry` uses would file per-session detail
as a durable trait of the athlete.

Pure module — no DB, no HTTP, no LLM — so the API, the prompts and the
persistence gate all read the same rule.
"""

from __future__ import annotations

# Recorded in ``ride_metrics.classification_confidence``. It sits outside the
# high/medium/low scale on purpose: those grade an inference we made, and this
# is not an inference. It is also the marker that stops a re-import overwriting
# the answer — see ``crud.upsert_ride_metric``.
ATHLETE_STATED_CONFIDENCE = "athlete"

# ``ride_metrics.purpose_question_status``. NULL means the question is still
# open; both terminal values stop it being asked again.
QUESTION_ANSWERED = "answered"
QUESTION_SKIPPED = "skipped"

# What the athlete may answer. Deliberately the vocabulary the rest of the app
# already reasons about (``classify_ride_purpose``) rather than free text: an
# answer that nothing downstream can read would leave the question just as
# consequence-free as the prose version was.
#
# The browser mirrors these values with its own display labels, the way it
# already does for ``feel_legs``; ``schemas.RidePurposeAnswerRequest`` is the
# enforcement point, and ``test_ride_purpose_question.py`` pins the exact set.
ATHLETE_PURPOSE_CHOICES: tuple[str, ...] = (
    "recovery",
    "endurance",
    "tempo",
    "interval_sweetspot",
    "interval_threshold",
    "interval_vo2max",
    "interval_sprints",
    "mixed",
)

# Purposes that mean "we could not work this out". Anything else — including the
# non-cycling families #578 introduced — is a statement, not a question.
_UNCERTAIN_PURPOSES = frozenset({None, "", "unknown"})


def question_is_open(
    *,
    ride_purpose: str | None,
    classification_confidence: str | None,
    purpose_question_status: str | None,
) -> bool:
    """Should this session still be asking the athlete what it was?

    Open when the classification is genuinely unsure — no purpose at all, or one
    the classifier itself graded ``low`` — and the athlete has neither answered
    nor waved it away.

    Deliberately *not* open for ``medium``: the coach asking about every
    half-certain endurance ride is how a useful question becomes wallpaper. And
    never open once the athlete has spoken, which is what
    :data:`ATHLETE_STATED_CONFIDENCE` records.
    """
    if purpose_question_status in (QUESTION_ANSWERED, QUESTION_SKIPPED):
        return False
    if classification_confidence == ATHLETE_STATED_CONFIDENCE:
        return False
    if ride_purpose in _UNCERTAIN_PURPOSES:
        return True
    return classification_confidence == "low"


def athlete_answer_reason(purpose: str) -> str:
    """The classification reason stored when the athlete answers.

    Written in a form the coach prompt can quote back, and phrased so that the
    reason for the label reads as evidence rather than as a residual doubt —
    because that is what it is.
    """
    label = purpose.replace("_", " ")
    return f"The athlete said this session was {label}."
