"""A short answer to a pinned question is judged in the athlete's language (ai-trainer-ops#258).

The judge read the language from the answer alone. "5 h" or "Noch müde" names
none, so the coach's reply and its rephrased question were left to the model's
guess. The athlete's earlier messages are read too now, then the interface.
"""

from __future__ import annotations

import json

import pytest

import crud
from services import ai_service
from tests.conftest import TestSessionLocal
from tests.test_athlete_inquiries import _current_user_id, _record

VERDICT = {"answered": True, "fact": "Rides about five hours a week.", "reply": "Danke!", "question": ""}


def _systems(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        seen.append(system_prompt)
        return json.dumps(VERDICT)

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "earlier", "interface", "named"),
    [
        ("5 h", ["Ich fahre meistens am Wochenende, unter der Woche eher kurz."], None, "German"),
        ("5 h", [], "German", "German"),
        ("About five hours a week, I think, and more in summer.", [], "German", "English"),
        ("5 h", [], None, None),
    ],
)
async def test_the_judge_names_the_language_of_the_answer_then_the_earlier_words_then_the_interface(
    monkeypatch, answer, earlier, interface, named
):
    seen = _systems(monkeypatch)

    await ai_service.evaluate_inquiry_answer(
        "How many hours do you ride a week?", answer, earlier=earlier, interface_language=interface
    )

    if named:
        assert f"The athlete writes {named}" in seen[0]
    else:
        assert "The athlete writes" not in seen[0]


@pytest.mark.asyncio
async def test_the_answer_route_hands_on_the_earlier_words_and_the_interface(client, auth_headers, monkeypatch):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(user_id, question="How many hours do you ride a week?")
    async with TestSessionLocal() as db:
        await crud.create_chat_message(
            db, user_id, role="user", content="Ich fahre meistens am Wochenende.", timestamp="2026-10-25T08:00:00Z"
        )
        await db.commit()
    told: dict = {}

    async def fake_evaluate(question, answer, **kwargs):
        told.update(kwargs)
        return VERDICT

    monkeypatch.setattr(ai_service, "evaluate_inquiry_answer", fake_evaluate)

    response = await client.post(
        f"/api/v1/users/me/inquiries/{inquiry.id}/answer",
        headers={**auth_headers, "X-App-Language": "de"},
        json={"answer": "5 h"},
    )

    assert response.status_code == 200
    assert told["earlier"] == ["Ich fahre meistens am Wochenende."]
    assert told["interface_language"] == "German"
