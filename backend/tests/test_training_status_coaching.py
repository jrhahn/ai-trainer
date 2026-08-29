"""The coach must know the badge it wrote, and must be able to explain it (#499).

The bug these tests exist for: the dashboard rendered "Slightly behind" from a
browser-side heuristic the backend never saw, so when the athlete asked why, the
coach denied being behind and attributed the label to model confidence and to
missing test data — an explanation that had nothing to do with the actual cause.
"""

from __future__ import annotations

import pytest

from services import ai_service, prompts, training_status
from services.prompts import (
    TRAINING_STATUS_LABEL_MAX_CHARS,
    ask_trainer_plan_updates_rule,
    ask_trainer_system,
    training_status_section,
)

FACTS = {
    "windowStart": "2026-07-24",
    "windowEnd": "2026-07-30",
    "plannedDue": 3,
    "plannedDone": 3,
    "adherence": 1.0,
    "sessions": [
        {
            "date": "2026-07-28",
            "slot": 0,
            "title": "VO2 Max Intervals",
            "workoutType": "intervals",
            "optional": False,
            "status": "done",
            "evidence": "matched activity Darmstadt Mountain Biking",
        }
    ],
    "missed": [],
    "skippedOptional": [],
    "pending": [],
    "extraActivities": [],
}


def _coach_prompt(badge=None) -> str:
    return ask_trainer_system(
        profile={"currentFTP": 280},
        today="2026-07-30",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        training_status_badge=badge,
    )


# ---------------------------------------------------------------------------
# The coach sees the badge
# ---------------------------------------------------------------------------


def test_coach_prompt_carries_the_badge_the_athlete_can_see():
    prompt = _coach_prompt(
        ("Slightly behind", "caution", "You missed Tuesday's threshold session.")
    )

    assert "Slightly behind" in prompt
    assert "You missed Tuesday's threshold session." in prompt


def test_coach_is_told_not_to_blame_fitness_or_model_confidence():
    """The exact confabulations the athlete got are ruled out explicitly."""
    prompt = _coach_prompt(("Slightly behind", "caution", "You missed one session."))

    lowered = prompt.lower()
    assert "do not deny it" in lowered
    assert "model confidence" in lowered
    assert "planned sessions executed" in lowered


def test_no_badge_section_when_the_athlete_has_no_badge():
    assert training_status_section(None, None, None) == ""
    assert "Training-status badge" not in _coach_prompt(None)


def test_badge_section_survives_a_missing_rationale():
    section = training_status_section("On track", "positive", None)

    assert "On track" in section
    assert "positive" in section


# ---------------------------------------------------------------------------
# The badge prompt itself
# ---------------------------------------------------------------------------


def test_status_prompt_states_the_judgement_rules():
    system = prompts.training_status_system()

    lowered = system.lower()
    assert "optional" in lowered
    assert "unplanned training still counts" in lowered
    assert str(TRAINING_STATUS_LABEL_MAX_CHARS) in system


def test_status_prompt_offers_every_tone_the_dashboard_can_paint():
    """A tone the prompt never names is a colour the athlete never sees.

    The dashboard colours the whole training summary from this value (#623), so
    a vocabulary that drifts out of the prompt silently retires a state.
    """
    system = prompts.training_status_system()

    for tone in prompts.TRAINING_STATUS_TONES:
        assert f'"{tone}"' in system, f"{tone} is a valid tone the prompt never offers"


def test_status_prompt_separates_a_missed_session_from_a_lost_block():
    """Red has to be earned, or it stops meaning anything."""
    system = prompts.training_status_system().lower()

    assert "alert" in system
    assert "majority" in system


@pytest.mark.asyncio
async def test_the_loudest_tone_survives_the_guardrails(monkeypatch):
    """`alert` is newer than the validator; it must not be scrubbed to steady."""
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _fake_chat(
            '{"label": "Behind plan", "tone": "alert",'
            ' "rationale": "You rode one of the five sessions that fell due."}'
        ),
    )

    result = await ai_service.generate_training_status(FACTS)

    assert result is not None
    assert result[1] == "alert"


def test_status_user_message_anchors_every_audited_session_to_a_weekday():
    """Sessions carry weekday anchors so the coach never derives one from an
    ISO date in its head (the #462 class of bug)."""
    message = prompts.training_status_user(FACTS, timezone_name="Europe/Berlin")

    assert "weekday" in message
    assert "Tuesday" in message  # 2026-07-28
    assert '"plannedDue": 3' in message


# ---------------------------------------------------------------------------
# Guardrails on what the model returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlong_label_is_rejected_rather_than_truncated(monkeypatch):
    """A badge that wraps breaks the dashboard's one-line status strip, and a
    truncated one stops reading as a phrase — so it is refused outright."""
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _fake_chat(
            '{"label": "You are quite a long way behind the plan right now",'
            ' "tone": "caution", "rationale": "Because reasons that are long."}'
        ),
    )

    assert await ai_service.generate_training_status(FACTS) is None


@pytest.mark.asyncio
async def test_missing_rationale_is_rejected(monkeypatch):
    monkeypatch.setattr(
        ai_service, "_chat", _fake_chat('{"label": "On track", "tone": "positive"}')
    )

    assert await ai_service.generate_training_status(FACTS) is None


@pytest.mark.asyncio
async def test_unknown_tone_falls_back_to_steady(monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _fake_chat(
            '{"label": "Cruising", "tone": "euphoric", "rationale": "All done."}'
        ),
    )

    assert await ai_service.generate_training_status(FACTS) == (
        "Cruising",
        "steady",
        "All done.",
    )


@pytest.mark.asyncio
async def test_usable_badge_is_returned_intact(monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _fake_chat(
            '{"label": "Ahead of plan.", "tone": "positive",'
            ' "rationale": "You added Saturday\'s long ride on top."}'
        ),
    )

    label, tone, rationale = await ai_service.generate_training_status(FACTS)

    # Trailing punctuation is stripped: the badge is a phrase, not a sentence.
    assert label == "Ahead of plan"
    assert tone == "positive"
    assert rationale == "You added Saturday's long ride on top."


def _fake_chat(payload: str):
    async def _chat(*_args, **_kwargs):
        return payload

    return _chat


# ---------------------------------------------------------------------------
# The badge is never blank
# ---------------------------------------------------------------------------


def test_fallback_covers_a_provider_failure():
    """The badge is part of first paint, so an unusable provider response must
    still leave something renderable behind."""
    label, tone, rationale = training_status.fallback_status(FACTS)

    assert label and tone and rationale
    assert len(label) <= TRAINING_STATUS_LABEL_MAX_CHARS
