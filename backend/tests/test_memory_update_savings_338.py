"""The notes update spends nothing on a thanks and little on an exchange with
nothing new (review of 2026-11-01, ai-trainer-ops#338 C8)."""

from __future__ import annotations

import logging

from unittest.mock import AsyncMock, patch

import pytest

import services.ai_service as ai_service
from services import coach_memory
from services.prompts import update_memory_system

NOTES = "Schedule constraints:\n- Rides in the morning."


@pytest.mark.parametrize(
    "message",
    [
        "Danke!",
        "Super, vielen Dank 👍",
        "danke schön",
        "Thanks a lot!",
        "ok thx",
        "Thank you, coach.",
        "Merci 🙏",
    ],
)
def test_a_message_that_only_thanks_is_read(message):
    assert coach_memory.only_thanks(message)


@pytest.mark.parametrize(
    "message",
    [
        "",
        "Ok",
        "Ja",
        "Super",
        "Danke, ich fahre ab jetzt morgens.",
        "Thanks, my FTP is 250 now.",
        "Danke, 3 Stunden passen.",
        "Danke, aber ich bin krank.",
    ],
)
def test_anything_beyond_thanks_still_goes_to_the_writer(message):
    assert not coach_memory.only_thanks(message)


@pytest.mark.asyncio
async def test_a_thanks_makes_no_call():
    chat = AsyncMock(return_value="anything")

    with patch.object(ai_service, "_chat", chat):
        result = await ai_service.update_coach_memory(NOTES, "Danke!", "Gern!")

    assert result == NOTES
    chat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["UNCHANGED", " unchanged.\n", "UNCHANGED."])
async def test_the_writers_unchanged_keeps_the_notes(reply, caplog):
    """An answer, not a rejected reply: no warning about a bad writer."""
    chat = AsyncMock(return_value=reply)

    with (
        patch.object(ai_service, "_chat", chat),
        caplog.at_level(logging.WARNING, logger=ai_service.logger.name),
    ):
        result = await ai_service.update_coach_memory(
            NOTES, "Wie war die Woche?", "Gut."
        )

    assert result == NOTES
    chat.assert_awaited_once()
    assert "rejected" not in caplog.text


def test_the_writer_is_told_to_answer_unchanged_with_one_word():
    assert "return only the single word UNCHANGED" in update_memory_system()
