"""The night's plan texts are written in the athlete's language too (ai-trainer-ops#271).

A plan the night rewrites has new texts, and the page asked for their German
only when the athlete opened it, so a German tester met them in English first.
The app's interface language is kept on the account when it opens, as the time
zone is, and the nightly run translates its new texts for it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from services import ai_service, plan_maintenance, plan_translations
from tests.conftest import TestSessionLocal
from tests.test_plan_maintenance import _create_user_with_plan, _day

NOW = datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc)


async def _reads(user_id: str, language: str | None) -> None:
    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        user.interface_language = language
        await db.commit()


@pytest.fixture
def night(monkeypatch):
    """A nightly run that moves the 8th's ride to the 9th, and a translator
    that records what it was asked for."""
    asked: list[list[str]] = []

    async def adapt(plan, *args, **kwargs):
        return [
            {**plan[0], "date": "2026-06-09", "title": "Moved ride"},
            {**plan[0], "workoutType": "rest", "title": "Rest", "durationMinutes": 0},
        ]

    async def translate(texts, language, provider="openai"):
        asked.append(list(texts))
        assert language == "German"
        return {text: f"DE {text}" for text in texts}

    async def no_weather(*args, **kwargs):
        return ""

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", adapt)
    monkeypatch.setattr(ai_service, "translate_plan_texts", translate)
    monkeypatch.setattr(
        plan_maintenance, "training_weather_context_for_user", no_weather
    )
    return asked


@pytest.mark.asyncio
async def test_the_app_keeps_the_language_it_shows_when_it_opens(client, auth_headers):
    me = "/api/v1/users/me"

    await client.get(me, headers={**auth_headers, "X-App-Language": "de"})
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        assert user.interface_language == "German"

    await client.get(me, headers={**auth_headers, "X-App-Language": "en"})
    await client.get(me, headers=auth_headers)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        # A request that says nothing changes nothing.
        assert user.interface_language == "English"


@pytest.mark.asyncio
async def test_a_german_readers_new_plan_texts_are_written_in_the_night(night):
    user_id = await _create_user_with_plan(
        email="night@example.com", plan=[_day("2026-06-08"), _day("2026-06-10")]
    )
    await _reads(user_id, "German")

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert result.updated == 1
    assert len(night) == 1 and "Moved ride" in night[0]
    async with TestSessionLocal() as db:
        stored = await plan_translations.stored_translations(
            db, user_id, "de", ["Moved ride"]
        )
    assert stored == {"Moved ride": "DE Moved ride"}


@pytest.mark.asyncio
async def test_an_english_reader_or_an_unknown_one_costs_no_call(night):
    for email, language in (
        ("en@example.com", "English"),
        ("unknown@example.com", None),
    ):
        user_id = await _create_user_with_plan(
            email=email, plan=[_day("2026-06-08"), _day("2026-06-10")]
        )
        await _reads(user_id, language)

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert result.updated == 2
    assert night == []


@pytest.mark.asyncio
async def test_a_night_that_changes_nothing_translates_nothing(night, monkeypatch):
    async def nothing(*args, **kwargs):
        return []

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", nothing)
    user_id = await _create_user_with_plan(
        email="calm@example.com", plan=[_day("2026-06-08"), _day("2026-06-10")]
    )
    await _reads(user_id, "German")

    await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert night == []


@pytest.mark.asyncio
async def test_a_night_that_rebuilds_the_plan_writes_its_german_too(
    client, auth_headers, mock_ai_service, monkeypatch
):
    from tests.test_auto_rebuild_206 import _first_plan

    await _first_plan(client, auth_headers, mock_ai_service)
    await client.put(
        "/api/v1/users/me", headers=auth_headers, json={"fitnessLevel": "beginner"}
    )
    asked: list[list[str]] = []

    async def translate(texts, language, provider="openai"):
        asked.append(list(texts))
        return {text: f"DE {text}" for text in texts}

    monkeypatch.setattr(ai_service, "translate_plan_texts", translate)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        user.interface_language = "German"
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        updated = await plan_maintenance.maintain_user_training_plan(
            db, user, timezone_name="UTC"
        )
        await db.commit()

    assert updated is True
    assert mock_ai_service["generate_training_plan"].call_count == 1
    assert asked and "Rebuilt" in asked[0]
