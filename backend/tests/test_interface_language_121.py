"""The interface language is the reply language until the athlete has written (ai-trainer-ops#121).

After a German onboarding the account has said nothing in the chat, so the
summary and the first replies had no language to go by and came out English. The
browser now sends ``X-App-Language``; the athlete's own words still win.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import crud
from services import ai_service, summary_pipeline
from services.athlete_language import interface_language
from tests.conftest import TestSessionLocal
from tests.test_plan_pipeline import _create_user, _day


def _request(headers: dict) -> SimpleNamespace:
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()})


@pytest.mark.parametrize(
    ("value", "language"),
    [("de", "German"), ("de-DE", "German"), ("EN", "English"), ("en-GB", "English"), ("fr", None), ("", None)],
)
def test_the_header_names_german_or_english(value, language):
    assert interface_language(_request({"X-App-Language": value})) == language


def test_no_header_names_nothing():
    assert interface_language(_request({})) is None
    assert interface_language(object()) is None


@pytest.mark.asyncio
async def test_a_reply_with_nothing_to_go_by_follows_the_interface(monkeypatch):
    seen = {}

    async def chat_history(provider, system_prompt, messages, **_kwargs):
        seen["system"] = system_prompt
        return '{"thinking": "", "response": "ok"}'

    monkeypatch.setattr(ai_service, "_chat_history", chat_history)

    # "ok" says nothing about a language.
    await ai_service.ask_trainer("ok", [], {}, interface_language="German")
    assert "The athlete writes German" in seen["system"]

    await ai_service.ask_trainer("ok", [], {})
    assert "The athlete writes" not in seen["system"]


@pytest.mark.asyncio
async def test_the_athletes_own_words_win_over_the_interface(monkeypatch):
    seen = {}

    async def chat_history(provider, system_prompt, messages, **_kwargs):
        seen["system"] = system_prompt
        return '{"thinking": "", "response": "ok"}'

    monkeypatch.setattr(ai_service, "_chat_history", chat_history)

    await ai_service.ask_trainer(
        "Is the plan for tomorrow still the same, or should I rest?", [], {}, interface_language="German"
    )
    assert "The athlete writes English" in seen["system"]


@pytest.mark.asyncio
async def test_a_new_accounts_summary_follows_the_interface(monkeypatch):
    user_id = await _create_user("iface-language@example.com", [_day("2026-07-22")])
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(db, user_id, estimated_ftp=250)
        await db.commit()
    seen = {}

    async def fake_generate(**kwargs):
        seen.update(kwargs)
        return "Kurz und gut."

    monkeypatch.setattr(summary_pipeline.ai_service, "generate_login_summary", fake_generate)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await summary_pipeline.regenerate(db, user, provider="gemini", interface_language="German")
    assert seen["reply_language"] == "German"


@pytest.mark.asyncio
async def test_the_refresh_route_reads_the_header(client, auth_headers, monkeypatch):
    seen = {}

    async def fake_regenerate(db, user, **kwargs):
        seen.update(kwargs)
        return "Kurz und gut."

    monkeypatch.setattr(summary_pipeline, "regenerate", fake_regenerate)
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(db, me.json()["id"], estimated_ftp=250)
        await db.commit()

    await client.post("/api/v1/ai/refresh-login-summary", headers={**auth_headers, "X-App-Language": "de"})
    assert seen.get("interface_language") == "German"


@pytest.mark.asyncio
async def test_the_chat_route_passes_the_interface_language(client, auth_headers, mock_ai_service, monkeypatch):
    captured: dict = {}
    original = mock_ai_service["ask_trainer"]

    async def spy(*args, **kwargs):
        captured.update(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr("services.ai_service.ask_trainer", spy)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers={**auth_headers, "X-App-Language": "de"},
        json={"question": "ok"},
    )
    assert response.status_code == 200
    assert captured["interface_language"] == "German"
