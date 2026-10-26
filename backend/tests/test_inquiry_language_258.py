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

VERDICT = {
    "answered": True,
    "fact": "Rides about five hours a week.",
    "reply": "Danke!",
    "question": "",
}


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
        (
            "5 h",
            ["Ich fahre meistens am Wochenende, unter der Woche eher kurz."],
            None,
            "German",
        ),
        ("5 h", [], "German", "German"),
        (
            "About five hours a week, I think, and more in summer.",
            [],
            "German",
            "English",
        ),
        ("5 h", [], None, None),
    ],
)
async def test_the_judge_names_the_language_of_the_answer_then_the_earlier_words_then_the_interface(
    monkeypatch, answer, earlier, interface, named
):
    seen = _systems(monkeypatch)

    await ai_service.evaluate_inquiry_answer(
        "How many hours do you ride a week?",
        answer,
        earlier=earlier,
        interface_language=interface,
    )

    if named:
        assert f"The athlete writes {named}" in seen[0]
    else:
        assert "The athlete writes" not in seen[0]


@pytest.mark.asyncio
async def test_the_answer_route_hands_on_the_earlier_words_and_the_interface(
    client, auth_headers, monkeypatch
):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(user_id, question="How many hours do you ride a week?")
    async with TestSessionLocal() as db:
        await crud.create_chat_message(
            db,
            user_id,
            role="user",
            content="Ich fahre meistens am Wochenende.",
            timestamp="2026-10-25T08:00:00Z",
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


# A pinned question the coach raises on its own (ai-trainer-ops#258). Its prompt
# asked for the athlete's language and gave the model nothing to infer it from:
# the history, the facts and the questions asked before are all English.


@pytest.mark.parametrize(
    ("language", "named"),
    [
        (
            "German",
            'The athlete writes German: write "question", "whyAsking" and '
            '"settingsHint" in German.',
        ),
        (None, None),
    ],
)
def test_the_generator_names_the_language_when_it_is_known(language, named):
    from services.prompts import generate_inquiries_system

    system = generate_inquiries_system(2, language)

    if named:
        assert named in system
    else:
        assert "The athlete writes" not in system


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("said", "interface", "expected"),
    [
        (["Ich bin heute müde, die Beine sind schwer."], "English", "German"),
        ([], "German", "German"),
        ([], None, None),
    ],
    ids=["their-words", "the-interface", "nothing-known"],
)
async def test_raising_questions_asks_in_the_athletes_language(
    monkeypatch, said, interface, expected
):
    from datetime import datetime, timezone

    import models
    from services import athlete_inquiry
    from tests.test_athlete_inquiries import _create_user_with_history

    user_id = await _create_user_with_history(
        email=f"lang-{len(said)}-{interface}@example.com", activity_count=10
    )
    async with TestSessionLocal() as db:
        for text in said:
            await crud.create_chat_message(
                db, user_id, role="user", content=text, timestamp="2026-06-07T10:00:00Z"
            )
        user = await db.get(models.User, user_id)
        user.interface_language = interface
        await db.commit()
    languages: list[str | None] = []

    async def fake_generate(metrics_section, **kwargs):
        languages.append(kwargs["reply_language"])
        return []

    monkeypatch.setattr(
        athlete_inquiry.ai_service, "generate_athlete_inquiries", fake_generate
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        await athlete_inquiry.generate_user_inquiries(
            db, user, now=datetime(2026, 6, 8, tzinfo=timezone.utc)
        )

    assert languages == [expected]


@pytest.mark.asyncio
async def test_the_service_hands_the_language_to_the_prompt(monkeypatch):
    seen = []

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        seen.append(system_prompt)
        return json.dumps({"candidates": []})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    await ai_service.generate_athlete_inquiries(
        "Ride history.", reply_language="German"
    )

    assert "The athlete writes German" in seen[0]
