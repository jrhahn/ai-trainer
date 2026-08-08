"""When is the coach still asking what a session was, and what happens to the
answer? (#580)

The question used to be prose in the login summary — no answer field, no state,
no consequence. These tests pin the three things that turn it into an object:
when it is open, what an answer writes, and that nothing later overwrites it.
"""

from __future__ import annotations

import pytest

from services.ride_purpose_question import (
    ATHLETE_PURPOSE_CHOICES,
    ATHLETE_STATED_CONFIDENCE,
    QUESTION_ANSWERED,
    QUESTION_SKIPPED,
    athlete_answer_reason,
    question_is_open,
)


def _open(**overrides) -> bool:
    kwargs = {
        "ride_purpose": "unknown",
        "classification_confidence": "low",
        "purpose_question_status": None,
    }
    kwargs.update(overrides)
    return question_is_open(**kwargs)


def test_an_unclassified_session_is_asked_about():
    assert _open()


def test_a_session_with_no_classification_at_all_is_asked_about():
    assert _open(ride_purpose=None, classification_confidence=None)


def test_a_low_confidence_label_is_still_a_question():
    """"Short high-intensity effort; may be a warmup" is a guess the athlete can
    settle in one tap."""
    assert _open(ride_purpose="short_hard_effort", classification_confidence="low")


def test_a_confident_classification_is_not_a_question():
    assert not _open(ride_purpose="interval_threshold", classification_confidence="high")


def test_medium_confidence_is_deliberately_left_alone():
    """A coach that asks about every half-certain endurance ride turns a useful
    question into wallpaper. Only the genuinely unsure ones are worth a tap."""
    assert not _open(ride_purpose="endurance", classification_confidence="medium")


def test_a_non_cycling_session_is_never_asked_what_ride_it_was():
    """#578 made these ``high``; this is the guard that they stay out of it."""
    assert not _open(ride_purpose="strength", classification_confidence="high")


@pytest.mark.parametrize("status", [QUESTION_ANSWERED, QUESTION_SKIPPED])
def test_the_question_stops_once_it_has_been_dealt_with(status):
    """Skipping has to be as final as answering, or "not now" becomes "forever"."""
    assert not _open(purpose_question_status=status)


def test_an_athlete_stated_classification_closes_the_question_on_its_own():
    """Belt and braces: even if the status column were lost, their answer is
    still visible in the confidence and must not be re-asked."""
    assert not _open(
        ride_purpose="interval_vo2max",
        classification_confidence=ATHLETE_STATED_CONFIDENCE,
        purpose_question_status=None,
    )


def test_the_answer_vocabulary_is_the_classifier_s_own():
    """The browser mirrors these strings and the schema enforces them, so the
    exact set is a wire contract rather than an implementation detail."""
    assert set(ATHLETE_PURPOSE_CHOICES) == {
        "recovery",
        "endurance",
        "tempo",
        "interval_sweetspot",
        "interval_threshold",
        "interval_vo2max",
        "interval_sprints",
        "mixed",
    }
    # "unknown" is what the athlete is being asked to resolve, never an answer.
    assert "unknown" not in ATHLETE_PURPOSE_CHOICES


def test_the_reason_reads_as_evidence_rather_than_doubt():
    reason = athlete_answer_reason("interval_threshold")
    assert "athlete" in reason.lower()
    assert "interval threshold" in reason
