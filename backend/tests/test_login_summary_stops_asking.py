"""The login summary stops asking a question it cannot take an answer to (#580).

The coach used to end an unclassified-session note with "ask the athlete what
they actually did". That sentence was rendered as body text in a card with no
answer field, no state and no consequence. Now the question is an object on the
activity itself, so putting it here as well would only ask it twice, once in a
place where it can never be answered.
"""

from __future__ import annotations

from services.prompts import refresh_login_summary_system, refresh_login_summary_user
from services.ride_purpose_question import ATHLETE_STATED_CONFIDENCE


def _summary_user(**overrides) -> str:
    kwargs = {
        "ride_insights": "some narrative",
        "last_ride_feedback": None,
        "notes": None,
        "estimated_ftp": 320,
        "training_plan": None,
        "latest_ride_purpose": "unknown",
        "latest_ride_confidence": "low",
        "latest_ride_reason": "Insufficient stream data to classify ride reliably.",
    }
    kwargs.update(overrides)
    return refresh_login_summary_user(**kwargs)


def test_the_unconfirmed_guardrail_survives():
    """The half that works stays: never assert a type that was not established."""
    msg = _summary_user()
    assert "could not be reliably auto-classified" in msg
    assert "Do NOT state or imply a specific session type" in msg


def test_the_summary_no_longer_asks_what_the_athlete_did():
    msg = _summary_user()
    assert "ask the athlete what they actually did" not in msg
    assert "what the intervals were" not in msg


def test_the_system_prompt_says_where_the_question_now_lives():
    """Told only "do not ask", a model asks anyway when it feels it must. Told
    the athlete is being asked elsewhere, it has somewhere to defer to."""
    system = refresh_login_summary_system()
    assert "Do NOT ask the athlete what they did in this summary" in system
    assert "on the activity itself" in system


def test_an_answered_session_is_stated_as_fact_not_hedged():
    """A summary that hedges about a session its own athlete just named reads as
    not listening."""
    msg = _summary_user(
        latest_ride_purpose="interval_threshold",
        latest_ride_confidence=ATHLETE_STATED_CONFIDENCE,
        latest_ride_reason="The athlete said this session was interval threshold.",
    )
    assert "The athlete stated what the most recent activity was" in msg
    assert "interval threshold" in msg
    assert "could not be reliably auto-classified" not in msg


def test_a_confident_classification_is_untouched():
    msg = _summary_user(
        latest_ride_purpose="interval_vo2max", latest_ride_confidence="high"
    )
    assert "could not be reliably auto-classified" not in msg
    assert "The athlete stated" not in msg
