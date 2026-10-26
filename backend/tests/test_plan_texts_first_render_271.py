"""A new plan comes up in the athlete's language at once (ai-trainer-ops#271).

Plan days are written in English, and the page asked for their German only
after the plan arrived: after onboarding, a German tester read English for
seconds to a minute. A plan generated or rebuilt for a German interface is
now translated before the answer leaves, and GET /users/me/plan carries the
translations already written, so the page has them with the plan.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

import crud
import models
from services import ai_service, plan_translations, rest_days
from services.ai_service import AIRateLimitError
from services.dates import app_today
from tests.conftest import TestSessionLocal
from tests.test_auto_rebuild_206 import REBUILD, _first_plan
from tests.test_coach_texts_german_215 import GERMAN, fake_translator  # noqa: F401

PLAN = "/api/v1/users/me/plan"
GENERATE = "/api/v1/ai/generate-plan"
TRANSLATIONS = "/api/v1/ai/plan-translations"


def _days() -> list[dict]:
    today = app_today()
    return [
        {
            "date": today.isoformat(),
            "workoutType": "endurance",
            "title": "Endurance Ride",
            "description": "Steady Z2.",
            "durationMinutes": 60,
        },
        {
            "date": (today + timedelta(days=1)).isoformat(),
            "workoutType": "intervals",
            "title": "Sweet Spot",
            "durationMinutes": 75,
        },
    ]


@pytest.mark.asyncio
async def test_a_plan_generated_for_a_german_interface_comes_with_its_german(
    client,
    auth_headers,
    mock_ai_service,
    fake_translator,  # noqa: F811
):
    mock_ai_service["generate_training_plan"].return_value = _days()

    response = await client.post(GENERATE, headers={**auth_headers, **GERMAN}, json={})

    assert response.status_code == 200
    assert len(fake_translator) == 1
    assert {"Endurance Ride", "Steady Z2.", "Sweet Spot"} <= set(fake_translator[0])
    # The first read of the plan carries them, with no call of its own.
    translations = (await client.get(PLAN, headers={**auth_headers, **GERMAN})).json()[
        "translations"
    ]
    assert translations["Endurance Ride"] == "DE Endurance Ride"
    assert translations["Sweet Spot"] == "DE Sweet Spot"
    # So the page finds nothing missing, and asks for no write.
    asked = (await client.get(TRANSLATIONS, headers={**auth_headers, **GERMAN})).json()
    assert asked["missing"] is False
    assert len(fake_translator) == 1


@pytest.mark.asyncio
async def test_an_english_interface_gets_its_plan_without_a_translation(
    client,
    auth_headers,
    mock_ai_service,
    fake_translator,  # noqa: F811
):
    mock_ai_service["generate_training_plan"].return_value = _days()

    assert (
        await client.post(GENERATE, headers=auth_headers, json={})
    ).status_code == 200

    for headers in (auth_headers, {**auth_headers, "X-App-Language": "en"}):
        assert (await client.get(PLAN, headers=headers)).json()["translations"] == {}
    assert fake_translator == []


@pytest.mark.asyncio
async def test_a_failed_translation_leaves_the_plan_and_the_page_asks_as_before(
    client, auth_headers, mock_ai_service, monkeypatch
):
    async def limited(texts, language, provider="openai"):
        raise AIRateLimitError("rate limited")

    monkeypatch.setattr(ai_service, "translate_plan_texts", limited)
    mock_ai_service["generate_training_plan"].return_value = _days()

    response = await client.post(GENERATE, headers={**auth_headers, **GERMAN}, json={})

    assert response.status_code == 200
    assert {"Endurance Ride", "Sweet Spot"} <= {day["title"] for day in response.json()}
    asked = (await client.get(TRANSLATIONS, headers={**auth_headers, **GERMAN})).json()
    assert asked["missing"] is True
    assert "Endurance Ride" not in asked["translations"]


@pytest.mark.asyncio
async def test_the_plan_is_committed_before_the_translation_is_asked_for(
    client, auth_headers, mock_ai_service, monkeypatch
):
    """The translation is one more model call, and the plan's row stays locked
    until the request's transaction ends: it is committed first."""
    seen: list[bool] = []

    async def translate(texts, language, provider="openai"):
        async with TestSessionLocal() as other:
            row = await other.scalar(select(models.TrainingPlan))
            seen.append(
                row is not None and "Sweet Spot" in {day["title"] for day in row.plan}
            )
        return {}

    monkeypatch.setattr(ai_service, "translate_plan_texts", translate)
    mock_ai_service["generate_training_plan"].return_value = _days()

    assert (
        await client.post(GENERATE, headers={**auth_headers, **GERMAN}, json={})
    ).status_code == 200

    assert seen == [True]


@pytest.mark.asyncio
async def test_a_rebuild_for_a_german_interface_comes_with_its_german(
    client,
    auth_headers,
    mock_ai_service,
    fake_translator,  # noqa: F811
):
    await _first_plan(client, auth_headers, mock_ai_service)
    await client.put("/api/v1/users/me", headers=auth_headers, json={"currentFTP": 280})
    assert fake_translator == []

    body = (
        await client.post(REBUILD, headers={**auth_headers, **GERMAN}, json={})
    ).json()

    assert body["rebuilt"] is True
    translations = (await client.get(PLAN, headers={**auth_headers, **GERMAN})).json()[
        "translations"
    ]
    assert translations["Rebuilt"] == "DE Rebuilt"


@pytest.mark.asyncio
async def test_the_plan_carries_the_written_and_the_fixed_german_without_a_call(
    client,
    auth_headers,
    fake_translator,  # noqa: F811
):
    """The rest-day guard's texts read German with no model call (ai-trainer-ops#265)."""
    today = app_today()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        await crud.upsert_training_plan(
            db,
            user.id,
            [
                {
                    "date": today.isoformat(),
                    "workoutType": "rest",
                    "title": rest_days.REST_TITLE,
                    "durationMinutes": 0,
                },
                {
                    "date": (today + timedelta(days=1)).isoformat(),
                    "workoutType": "endurance",
                    "title": "Endurance Ride",
                    "durationMinutes": 60,
                },
                {
                    "date": (today + timedelta(days=2)).isoformat(),
                    "workoutType": "endurance",
                    "title": "Not written yet",
                    "durationMinutes": 60,
                },
            ],
        )
        await crud.save_plan_text_translations(
            db,
            user.id,
            "de",
            {plan_translations.source_hash("Endurance Ride"): "Grundlagenfahrt"},
        )
        await db.commit()

    plan = (await client.get(PLAN, headers={**auth_headers, **GERMAN})).json()

    assert plan["translations"] == {
        rest_days.REST_TITLE: "Ruhetag",
        "Endurance Ride": "Grundlagenfahrt",
    }
    assert [day["title"] for day in plan["plan"]] == [
        rest_days.REST_TITLE,
        "Endurance Ride",
        "Not written yet",
    ]
    # The same account in English reads none of them.
    for headers in (auth_headers, {**auth_headers, "X-App-Language": "en"}):
        assert (await client.get(PLAN, headers=headers)).json()["translations"] == {}
    assert fake_translator == []
