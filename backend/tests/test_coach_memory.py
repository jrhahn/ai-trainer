"""The coach's notes: whole in the prompts, and only notes replace them (ai-trainer-ops#119)."""

import json
from unittest.mock import patch

import pytest

import crud
from auth import decode_token
from routers.users import races
from services import ai_service, coach_memory
from services.ai_service import update_coach_memory as real_update_coach_memory
from services.prompts import next_ride_recommendation_user, update_memory_system
from tests.conftest import TestSessionLocal

SCHEDULE = "Mittwochs kein Training (Chorprobe). Freitags max. 45 min."
# More than 1,000 characters, the schedule first, as the writer files them: the
# chat kept only the last 800 characters and lost the schedule.
NOTES = (
    f"Schedule constraints:\n- {SCHEDULE}\n- Long rides only on Sundays, up to 4 h.\n"
    "- Commutes by bike on Tuesdays and Thursdays, 30 min each way.\n"
    "Fatigue & intensity response:\n- Recovers slowly after VO2max days; needs two "
    "easy days after a hard block.\n"
    "Hydration, fueling & heat response:\n- Ran out of drink on a 5 h ride above 30 C; "
    "plan 750 ml per hour with sodium on hot days.\n"
    "Preferred workout types:\n- Loves long climbs and steady tempo; dislikes short "
    "sprints on the trainer.\n- Prefers indoor intervals on weekdays in winter.\n"
    "Recurring issues:\n- Over-paces the first hour of endurance rides and skips cooldowns.\n"
    "Psychological training tendencies:\n- May need reassurance after two or more rest "
    "days; tends to add extra intervals when feeling fresh.\n"
    "FTP & target context:\n- FTP 265 W (August), 272 W (September).\n"
    "Race & event priorities:\n- A race: Ötztaler Radmarathon, end of August.\n"
    "Goals & motivations:\n- Finish the Ötztaler under 9 h; enjoys group rides.\n"
    "- Wants to ride the Dolomites with friends next summer."
)
PROFILE = {"fitnessLevel": "intermediate", "currentFTP": 270}


def _reply(text: str):
    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        return text

    return fake_chat


def test_the_notes_are_the_size_the_issue_measured():
    assert len(NOTES) > 1000
    assert NOTES.index(SCHEDULE) < len(NOTES) - 800


@pytest.mark.asyncio
async def test_the_chat_carries_the_whole_notes():
    captured: list[str] = []

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        captured.append(system_prompt)
        return json.dumps({"response": "Noted.", "planUpdates": [], "sources": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        await ai_service.ask_trainer(
            question="Kann ich Mittwoch fahren?",
            plan=[],
            profile=PROFILE,
            coach_memory=NOTES,
        )

    assert SCHEDULE in captured[-1]
    assert "Goals & motivations" in captured[-1]


def test_the_next_ride_carries_the_whole_notes():
    prompt = next_ride_recommendation_user(
        rides=[], plan=[], profile=PROFILE, rider_assessment=None, coach_memory=NOTES
    )

    assert SCHEDULE in prompt
    assert "Goals & motivations" in prompt


def test_notes_past_the_limit_keep_their_beginning_and_say_they_were_cut():
    long_notes = NOTES + "\n" + "- More notes.\n" * 400

    prompt = next_ride_recommendation_user(
        rides=[],
        plan=[],
        profile=PROFILE,
        rider_assessment=None,
        coach_memory=long_notes,
    )

    assert SCHEDULE in prompt
    assert "…[cut]" in prompt
    assert prompt.count("- More notes.") < 400


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("I'm sorry, I can't help with that request.", "no heading"),
        ("Keine neuen Informationen.", "no heading"),
        ("", "empty"),
        ("   \n", "empty"),
        # Cut off after the schedule.
        (NOTES[: NOTES.index("Fatigue")], "drops a heading"),
        # Every heading, nothing under them.
        (
            "\n".join(f"{heading}:" for heading in coach_memory.HEADINGS[:9]),
            "much shorter",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_reply_that_is_not_notes_leaves_them(reply, reason):
    assert coach_memory.rejection(NOTES, reply) == reason
    with patch.object(ai_service, "_chat", side_effect=_reply(reply)):
        result = await ai_service.update_coach_memory(NOTES, "Hallo", "Hallo!")

    assert result == NOTES


@pytest.mark.asyncio
async def test_a_first_reply_without_a_heading_writes_nothing():
    with patch.object(
        ai_service, "_chat", side_effect=_reply("Prefers morning rides.")
    ):
        assert (
            await ai_service.update_coach_memory("", "I ride mornings.", "Noted.") == ""
        )


@pytest.mark.parametrize(
    "reply",
    [
        NOTES + "\n- Prefers morning rides.",
        # The writer's own variants of a heading.
        NOTES.replace(
            "Fatigue & intensity response:", "**Fatigue and intensity response**"
        )
        .replace("Schedule constraints:", "## Schedule Constraints")
        .replace("Goals & motivations:", "1. Goals & motivations:"),
    ],
)
@pytest.mark.asyncio
async def test_notes_replace_notes(reply):
    with patch.object(ai_service, "_chat", side_effect=_reply(f"\n{reply}\n")):
        assert await ai_service.update_coach_memory(NOTES, "Hallo", "Hallo!") == reply


@pytest.mark.asyncio
async def test_the_same_notes_come_back_as_they_were_stored():
    """Whitespace aside, unchanged notes are the stored string: no write (review of #930)."""
    stored = NOTES + "\n"
    with patch.object(ai_service, "_chat", side_effect=_reply(NOTES)):
        assert await ai_service.update_coach_memory(stored, "Hallo", "Hallo!") is stored


def test_short_notes_may_lose_a_line():
    """One removed line can be half of short notes: the share does not judge them."""
    notes = "Schedule constraints:\n- No Wednesdays.\n- Fridays 45 min at most."
    assert len(notes) < coach_memory.SHARE_FROM_CHARS

    assert (
        coach_memory.rejection(notes, "Schedule constraints:\n- No Wednesdays.") is None
    )


def test_the_headings_are_the_writers_and_the_race_calendars():
    system = update_memory_system()

    for heading in coach_memory.HEADINGS[:-1]:
        assert f"- {heading}:" in system
    assert races._RACE_MEMORY_HEADING == f"{coach_memory.HEADINGS[-1]}:"


async def _chat_with_memory_reply(client, auth_headers, monkeypatch, reply: str) -> str:
    """The stored notes after one chat turn whose memory writer says ``reply``."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_coach_memory(db, user_id, NOTES)
        await db.commit()

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        return reply if system_prompt == update_memory_system() else "{}"

    monkeypatch.setattr(ai_service, "update_coach_memory", real_update_coach_memory)
    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    response = await client.post(
        "/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": "Hallo"}
    )
    assert response.status_code == 200
    async with TestSessionLocal() as db:
        return (await crud.get_coach_memory(db, user_id)).memory


@pytest.mark.asyncio
async def test_a_refusal_after_a_chat_turn_leaves_the_stored_notes(
    client, auth_headers, mock_ai_service, monkeypatch
):
    stored = await _chat_with_memory_reply(
        client, auth_headers, monkeypatch, "I'm sorry, I can't help with that request."
    )

    assert stored == NOTES


@pytest.mark.asyncio
async def test_notes_after_a_chat_turn_are_stored(
    client, auth_headers, mock_ai_service, monkeypatch
):
    """The same path stores a real update, so the refusal test is not passing by accident."""
    stored = await _chat_with_memory_reply(
        client, auth_headers, monkeypatch, NOTES + "\n- Prefers morning rides."
    )

    assert stored == NOTES + "\n- Prefers morning rides."
