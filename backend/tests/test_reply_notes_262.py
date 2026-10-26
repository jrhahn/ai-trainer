"""The app's notes sit beside a coach reply, not in it (ai-trainer-ops#262).

The router and the chat service appended their notes ("(Hinweis: …)",
"Hinweis: …") to the reply's text. It was stored that way and replayed to the
model as the coach's own words, with two language detections deciding the
notes' language. The notes are ``[{kind, text}]`` beside the reply now: in the
response, in ``ChatMessage.notes_json`` and in the chat history, never in the
text the model reads back.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

import crud
import models
from scripts import wave_gates_report
from services import reply_notes
from tests.conftest import TestSessionLocal

ASK = "/api/v1/ai/ask-trainer"
DAY = (date.today() + timedelta(days=2)).isoformat()


async def _with_a_plan(client, auth_headers) -> None:
    plan = {"plan": [{"date": DAY, "workoutType": "endurance", "title": "Ride", "durationMinutes": 60}]}
    assert (await client.put("/api/v1/users/me/plan", json=plan, headers=auth_headers)).status_code == 200


async def _assistant_rows() -> list[models.ChatMessage]:
    async with TestSessionLocal() as db:
        rows = await db.scalars(select(models.ChatMessage).where(models.ChatMessage.role == "assistant"))
        return list(rows.all())


@pytest.mark.asyncio
async def test_a_note_comes_beside_the_reply_and_is_stored_beside_it(client, auth_headers, mock_ai_service):
    await _with_a_plan(client, auth_headers)

    body = (await client.post(ASK, json={"question": "Can I rest instead?"}, headers=auth_headers)).json()

    [note] = body["notes"]
    assert note["kind"] == reply_notes.UNRESOLVED_DATES and "2026-04-10" in note["text"]
    assert body["response"] == "Take it easy tomorrow."
    [row] = await _assistant_rows()
    assert row.content == "Take it easy tomorrow."
    assert row.notes_json == body["notes"]
    history = (await client.get("/api/v1/users/me/chat", headers=auth_headers)).json()["messages"]
    assert history[-1]["notes"] == body["notes"] and history[0]["notes"] == []


@pytest.mark.asyncio
async def test_the_model_never_reads_a_note_as_its_own_words(client, auth_headers, mock_ai_service):
    await _with_a_plan(client, auth_headers)
    await client.post(ASK, json={"question": "Can I rest instead?"}, headers=auth_headers)

    await client.post(ASK, json={"question": "And on Sunday?"}, headers=auth_headers)

    history = mock_ai_service["ask_trainer"].await_args.kwargs["conversation_history"]
    assert history[-1] == {"role": "assistant", "content": "Take it easy tomorrow."}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "interface", "language", "words"),
    [
        ("Kann ich stattdessen heute ruhen und morgen fahren?", "en", "German", "konnte ich nicht ändern"),
        ("Can I rest instead today and ride tomorrow?", "de", "English", "could not update"),
        ("5 h?", "de", "German", "konnte ich nicht ändern"),
    ],
)
async def test_the_reply_and_the_notes_share_one_language(
    client, auth_headers, mock_ai_service, question, interface, language, words
):
    await _with_a_plan(client, auth_headers)

    body = (
        await client.post(
            ASK, json={"question": question}, headers={**auth_headers, "X-App-Language": interface}
        )
    ).json()

    assert mock_ai_service["ask_trainer"].await_args.kwargs["reply_language"] == language
    assert words in body["notes"][0]["text"]


@pytest.mark.asyncio
async def test_a_rewritten_reply_says_so(client, auth_headers, mock_ai_service):
    mock_ai_service["ask_trainer"].return_value = {"response": "Fine.", "plan_updates": [], "rewritten": True}

    body = (await client.post(ASK, json={"question": "Ok?"}, headers=auth_headers)).json()

    assert body["rewritten"] is True and body["notes"] == []


@pytest.mark.asyncio
async def test_the_wave_report_counts_notes_by_kind_and_still_reads_the_old_ones():
    since = date.today() - timedelta(days=1)
    async with TestSessionLocal() as db:
        user = models.User(email="wave-notes@example.com", name="Rider", hashed_password="x")
        db.add(user)
        await db.flush()
        for content, notes in (
            ("A reply.", [reply_notes.note(reply_notes.UNTRACED_NUMBERS, "312 W in this reply …")]),
            ("A reply.", [reply_notes.note(reply_notes.STANDING_PRIORS, "Your plan …")]),
            # Written before #262: the note is in the text.
            ("A reply.\n\nNote: 300 W in this reply could not be traced to your data.", None),
            ("A clean reply.", None),
        ):
            await crud.create_chat_message(
                db, user.id, role="assistant", content=content, timestamp="2026-10-26T08:00:00Z", notes=notes
            )
        await db.commit()

    async with TestSessionLocal() as db:
        figures = await wave_gates_report.gather(db, since, [])

    assert (figures.replies, figures.number_notes, figures.rule_notes) == (4, 2, 1)
