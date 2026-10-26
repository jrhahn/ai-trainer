"""The coach's memory: update_memory_system, update_coach_memory and extract_athlete_facts.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import services.ai_service as ai_service


@pytest.mark.asyncio
async def test_update_coach_memory_calls_chat():
    # Under the writer's headings: a reply without one is no notes and is not
    # stored (ai-trainer-ops#119).
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return "Schedule constraints:\n- Prefers morning rides.\nFTP & target context:\n- FTP ~280 W."

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.update_coach_memory(
            current_memory="",
            user_message="I always ride in the morning.",
            coach_response="Got it, I'll schedule morning sessions.",
            provider="openai",
        )

    assert "morning" in result.lower() or "280" in result


# ---------------------------------------------------------------------------
# Task 8: Improved coach memory – update_memory_system prompt categories
# ---------------------------------------------------------------------------


def test_update_memory_system_includes_schedule_constraints():
    """System prompt must guide the AI to capture schedule constraints."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "schedule" in text or "weekday" in text or "availability" in text


def test_update_memory_system_includes_fatigue_patterns():
    """System prompt must guide the AI to capture subjective fatigue / intensity response."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "fatigue" in text or "intensity" in text or "over-reaching" in text


def test_update_memory_system_includes_hydration_fueling_heat_context():
    """System prompt must guide the AI to retain actionable hydration/fueling context."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "hydration" in text
    assert "fueling" in text
    assert "heat" in text
    assert "drink volume" in text
    assert "running out of fluids" in text


def test_update_memory_system_includes_preferred_workout_types():
    """System prompt must guide the AI to capture preferred workout types."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "preferred workout" in text or "workout types" in text or "favourite" in text or "favorite" in text


def test_update_memory_system_includes_recurring_issues():
    """System prompt must guide the AI to capture recurring coaching issues."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "recurring" in text or "over-pacing" in text or "repeated" in text


def test_update_memory_system_includes_psychological_training_tendencies():
    """System prompt must capture durable psychological training patterns."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "psychological training tendencies" in text
    assert "overtraining" in text and "undertraining" in text
    assert "anxiety after multiple rest days" in text or "nervousness" in text
    assert "fomo" in text
    assert "overanalysis" in text
    assert "reassurance" in text
    assert "permission to rest" in text
    assert "too much when feeling fresh" in text
    assert "mtb" in text


def test_update_memory_system_includes_ftp_context():
    """System prompt must guide the AI to record FTP history."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "ftp" in text


def test_update_memory_system_includes_race_event_priorities():
    """System prompt must guide the AI to capture race and event priorities."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "race" in text or "event" in text or "priorities" in text


def test_update_memory_system_avoids_transient_details():
    """System prompt must explicitly instruct the AI not to store transient / one-off details."""
    from services.prompts import update_memory_system

    text = update_memory_system().lower()
    assert "transient" in text or "one-off" in text or "durable" in text
    assert "single event" in text
    assert "5-hour ride" in text
    assert "one-off transient moods" in text
    assert "permanent psychological traits" in text
    assert "confirms the pattern" in text or "repeats across exchanges" in text


def test_update_memory_system_uses_coach_persona():
    """update_memory_system must embed the COACH_PERSONA."""
    from services.prompts import update_memory_system, COACH_PERSONA

    assert COACH_PERSONA in update_memory_system()


def test_update_memory_user_includes_existing_notes_and_exchange():
    """update_memory_user must include existing notes and the latest exchange."""
    from services.prompts import update_memory_user

    msg = update_memory_user(
        current_memory="Prefers morning rides.",
        user_message="I can only ride 45 min on weekdays.",
        coach_response="Noted, I'll keep weekday sessions short.",
    )
    assert "Prefers morning rides." in msg
    assert "45 min on weekdays" in msg
    assert "weekday sessions short" in msg
    assert "recommendation clarification question" in msg


def test_update_memory_user_handles_empty_memory():
    """update_memory_user must not raise when current_memory is empty."""
    from services.prompts import update_memory_user

    msg = update_memory_user(current_memory="", user_message="Hi", coach_response="Hello!")
    assert "none" in msg.lower() or msg  # at minimum it must return a non-empty string


@pytest.mark.asyncio
async def test_update_coach_memory_passes_system_and_user_prompts():
    """update_coach_memory must call _chat with the system and user prompts."""
    from services.prompts import update_memory_system

    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return "Schedule constraints: weekdays limited to 45 min."

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.update_coach_memory(
            current_memory="",
            user_message="I can only ride 45 min on weekdays.",
            coach_response="Noted.",
            provider="openai",
        )

    assert len(captured) == 1
    system_prompt, user_msg = captured[0]
    assert system_prompt == update_memory_system()
    assert "45 min on weekdays" in user_msg
    assert "weekdays" in result.lower() or "45 min" in result.lower()


# ---------------------------------------------------------------------------
# extract_athlete_facts — import historical coach conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_athlete_facts_returns_normalised_candidates():
    captured: list[tuple[str, str]] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        captured.append((system_prompt, user_msg))
        return json.dumps({
            "candidates": [
                {
                    "fact": "Gets anxious after two rest days",
                    "category": "psychological_tendencies",
                    "confidence": 0.7,
                    "sourceSnippet": "I always feel like I'm losing fitness when I rest.",
                },
                {
                    # Confidence above the cap is clamped to 0.9.
                    "fact": "Prefers long Saturday endurance rides",
                    "category": "preferred_workouts",
                    "confidence": 1.5,
                },
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.extract_athlete_facts(
            "Athlete: I hate resting...\nCoach: Why?", provider="openai"
        )

    assert len(candidates) == 2
    assert candidates[0]["fact"] == "Gets anxious after two rest days"
    assert candidates[0]["category"] == "psychological_tendencies"
    assert candidates[0]["confidence"] == 0.7
    assert candidates[0]["source_snippet"].startswith("I always feel")
    # Confidence is clamped to the 0.9 ceiling.
    assert candidates[1]["confidence"] == 0.9
    # The transcript reaches the model.
    assert "I hate resting" in captured[0][1]


@pytest.mark.asyncio
async def test_extract_athlete_facts_dedupes_and_drops_invalid():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({
            "candidates": [
                {"fact": "Loves climbing", "category": "preferred_workouts", "confidence": 0.6},
                {"fact": "  loves CLIMBING ", "category": "preferred_workouts", "confidence": 0.8},
                {"fact": "", "category": "general", "confidence": 0.5},
                {"category": "general", "confidence": 0.5},
                "not a dict",
            ]
        })

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.extract_athlete_facts("transcript", provider="openai")

    # Duplicate (case/space-insensitive) and invalid entries are removed.
    assert len(candidates) == 1
    assert candidates[0]["fact"] == "Loves climbing"


@pytest.mark.asyncio
async def test_extract_athlete_facts_empty_transcript_skips_model():
    called = False

    async def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.extract_athlete_facts("   ", provider="openai")

    assert candidates == []
    assert called is False


@pytest.mark.asyncio
async def test_extract_athlete_facts_handles_malformed_payload():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"unexpected": "shape"})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        candidates = await ai_service.extract_athlete_facts("transcript", provider="openai")

    assert candidates == []
