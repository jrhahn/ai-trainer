"""The candidates of an imported conversation in the athlete's language
(ai-trainer-ops#258).

The extraction writes the candidate facts in English, like every fact the coach
keeps, and the athlete reviews them before anything is stored. They now come
with their translations into the interface language, written by the same
plan-text translation and stored with it, so a candidate kept as a trait is
already translated.
"""

from __future__ import annotations

import pytest

from services import ai_service, plan_constraints

GERMAN = {"X-App-Language": "de"}
FACT = "Gets anxious after two rest days"
TRANSCRIPT = {"transcript": "Athlete: I hate resting. Coach: Tell me more."}


@pytest.fixture
def candidates(mock_ai_service):
    mock_ai_service["extract_athlete_facts"].return_value = [
        {
            "fact": FACT,
            "category": "psychological_tendencies",
            "confidence": 0.7,
            "source_snippet": "I feel like I'm losing fitness when I rest.",
        }
    ]
    return mock_ai_service


@pytest.fixture
def fake_translator(monkeypatch):
    calls: list[list[str]] = []

    async def translate(texts, language, provider="openai"):
        calls.append(list(texts))
        return {text: f"DE {text}" for text in texts}

    monkeypatch.setattr(ai_service, "translate_plan_texts", translate)
    return calls


async def _extract(client, auth_headers, extra=None):
    response = await client.post(
        "/api/v1/ai/extract-athlete-facts",
        headers={**auth_headers, **(extra or {})},
        json=TRANSCRIPT,
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_a_german_account_gets_the_candidates_in_german(
    client, auth_headers, candidates, fake_translator
):
    body = await _extract(client, auth_headers, GERMAN)

    assert body["candidates"][0]["fact"] == FACT
    assert body["translations"] == {FACT: f"DE {FACT}"}
    assert fake_translator == [[FACT]]


@pytest.mark.asyncio
async def test_a_translation_is_written_once_and_kept_with_the_trait(
    client, auth_headers, candidates, fake_translator
):
    await _extract(client, auth_headers, GERMAN)
    again = await _extract(client, auth_headers, GERMAN)

    assert again["translations"] == {FACT: f"DE {FACT}"}
    assert fake_translator == [[FACT]], (
        "the stored translation is read, not written again"
    )

    kept = await client.post(
        "/api/v1/users/me/athlete-memory-facts",
        headers=auth_headers,
        json={"fact": FACT, "category": "psychological_tendencies"},
    )
    assert kept.status_code in (200, 201)
    shown = await client.get(
        "/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN}
    )
    assert shown.json()["translations"][FACT] == f"DE {FACT}"


@pytest.mark.asyncio
async def test_a_text_the_app_translates_by_hand_costs_no_call(
    client, auth_headers, mock_ai_service, fake_translator
):
    """The hand-written German wins, as for the plan (review of #1340)."""
    mock_ai_service["extract_athlete_facts"].return_value = [
        {"fact": plan_constraints.NO_BIKE_TITLE, "category": "general"}
    ]

    body = await _extract(client, auth_headers, GERMAN)

    assert body["translations"] == {
        plan_constraints.NO_BIKE_TITLE: plan_constraints.GERMAN[
            plan_constraints.NO_BIKE_TITLE
        ]
    }
    assert fake_translator == []


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [None, {"X-App-Language": "en"}], ids=["none", "en"])
async def test_an_untranslated_interface_costs_no_call(
    client, auth_headers, candidates, fake_translator, extra
):
    body = await _extract(client, auth_headers, extra)

    assert body["translations"] == {}
    assert fake_translator == []


@pytest.mark.asyncio
async def test_a_failed_translation_still_returns_the_candidates(
    client, auth_headers, candidates, monkeypatch
):
    async def broken(texts, language, provider="openai"):
        raise RuntimeError("model down")

    monkeypatch.setattr(ai_service, "translate_plan_texts", broken)

    body = await _extract(client, auth_headers, GERMAN)

    assert body["candidates"][0]["fact"] == FACT
    assert body["translations"] == {}
