"""The coach's notes are written in the athlete's language (ai-trainer-ops#258).

The athlete reads the notes in the app. The writer was never told their
language, and filed a German exchange in English 14 times out of 16.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

import routers.ai as ai_router
import services.ai_service as ai_service
from services import coach_memory, metrics
from services.llm import AIProviderError
from services.prompts import update_memory_user

ENGLISH_NOTES = (
    "Schedule constraints:\n- Rides on Tuesday and Thursday evenings, and the"
    " longer rides are on the weekend when there is time for them."
)
GERMAN_NOTES = (
    "Schedule constraints:\n- Fährt dienstags und donnerstags abends, und die"
    " langen Ausfahrten sind am Wochenende, wenn er Zeit hat."
)
QUESTION = "Ich habe nur Dienstag und Donnerstag abends Zeit."
ANSWER = "Dann legen wir die Intervalle auf diese Tage."


def test_the_writer_is_told_the_athletes_language():
    msg = update_memory_user(ENGLISH_NOTES, QUESTION, ANSWER, "German")

    assert "write every note under the headings in German" in msg
    assert "Translate any existing note in another language" in msg
    assert "the headings stay in English" in msg


def test_without_a_known_language_the_exchange_reads_as_before():
    msg = update_memory_user(ENGLISH_NOTES, QUESTION, ANSWER)

    assert msg == update_memory_user(ENGLISH_NOTES, QUESTION, ANSWER, None)
    assert "German" not in msg and "Translate" not in msg


def test_the_headings_do_not_count_as_the_notes_language():
    notes = "Schedule constraints:\n- Fährt abends.\n**Goals & motivations**\n- Ötzi"

    assert coach_memory.without_headings(notes) == "- Fährt abends.\n- Ötzi"


@pytest.mark.asyncio
async def test_german_notes_take_one_call():
    chat = AsyncMock(return_value=GERMAN_NOTES)

    with patch.object(ai_service, "_chat", chat):
        result = await ai_service.update_coach_memory(
            "", QUESTION, ANSWER, reply_language="German"
        )

    assert result == GERMAN_NOTES
    assert chat.await_count == 1
    assert "in German" in chat.await_args.args[2]


@pytest.mark.asyncio
async def test_english_notes_for_a_german_athlete_are_written_again():
    chat = AsyncMock(side_effect=[ENGLISH_NOTES, GERMAN_NOTES])

    with (
        patch.object(ai_service, "_chat", chat),
        patch.object(metrics, "record_coach_rewrite") as rewrite,
    ):
        result = await ai_service.update_coach_memory(
            "", QUESTION, ANSWER, reply_language="German"
        )

    assert result == GERMAN_NOTES
    assert chat.await_count == 2
    assert "Your notes were not in German" in chat.await_args.args[2]
    rewrite.assert_called_once_with(surface="notes", reason="language")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second",
    [
        ENGLISH_NOTES.replace("Tuesday", "Monday"),
        AIProviderError(key_refused=False, provider="gemini"),
    ],
)
async def test_the_first_notes_stand_when_the_second_attempt_does_not_help(second):
    chat = AsyncMock(side_effect=[ENGLISH_NOTES, second])

    with patch.object(ai_service, "_chat", chat):
        result = await ai_service.update_coach_memory(
            "", QUESTION, ANSWER, reply_language="German"
        )

    # Notes in the wrong language beat losing what they note.
    assert result == ENGLISH_NOTES
    assert chat.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("language", [None, "English"])
async def test_english_notes_stand_when_english_is_expected_or_unknown(language):
    chat = AsyncMock(return_value=ENGLISH_NOTES)

    with patch.object(ai_service, "_chat", chat):
        result = await ai_service.update_coach_memory(
            "", "I ride on Tuesday evenings.", "Noted.", reply_language=language
        )

    assert result == ENGLISH_NOTES
    assert chat.await_count == 1


@pytest.mark.asyncio
async def test_unchanged_is_no_language_to_correct():
    chat = AsyncMock(return_value="UNCHANGED")

    with patch.object(ai_service, "_chat", chat):
        result = await ai_service.update_coach_memory(
            GERMAN_NOTES, QUESTION, ANSWER, reply_language="German"
        )

    assert result == GERMAN_NOTES
    assert chat.await_count == 1


def test_the_chat_queues_the_notes_with_the_athletes_language(user):
    user.memory_updates_enabled = True
    tasks = BackgroundTasks()

    ai_router._remember_in_background(
        tasks, user, QUESTION, {"response": ANSWER}, "German"
    )

    [task] = tasks.tasks
    assert task.func is ai_router._update_memory_bg
    assert task.args[-1] == "German"


@pytest.mark.asyncio
async def test_the_background_update_hands_the_language_to_the_writer(monkeypatch):
    seen = {}

    async def fake_update(current_memory, user_message, coach_response, **kwargs):
        seen.update(kwargs)
        return current_memory

    monkeypatch.setattr(ai_router.ai_service, "update_coach_memory", fake_update)

    await ai_router._update_memory_attempts(
        "nobody", QUESTION, ANSWER, "gemini", "German"
    )

    assert seen == {"provider": "gemini", "reply_language": "German"}
