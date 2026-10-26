"""A coach text written while the app is open reads German too (ai-trainer-ops#215).

The page asked for translations only when a plan text changed, so a hypothesis
or a question the coach wrote during a chat stayed English until the next load,
and the expert mode's predictions were never translated. The page now asks
whenever it receives coach texts, and the server names the untranslated ones
with a key, so the page writes once per new set of texts, not once per read.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
from services import plan_translations
from tests.conftest import TestSessionLocal
from tests.test_coach_texts_german_215 import GERMAN, fake_translator  # noqa: F401

TRANSLATIONS = "/api/v1/ai/plan-translations"
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


def test_the_same_texts_have_the_same_key_in_any_order():
    key = plan_translations.missing_key(["Rest", "Spin", "Rest"])

    assert key == plan_translations.missing_key(["Spin", "Rest"])
    assert key != plan_translations.missing_key(["Spin", "Rest", "Tempo"])
    assert plan_translations.missing_key([]) is None


@pytest.mark.asyncio
async def test_a_language_that_is_not_translated_owes_nothing(db, user):
    assert await plan_translations.ensure_translations(
        db, user, "English", NOW.date(), source="test"
    ) == ({}, [])


@pytest.mark.asyncio
async def test_a_prediction_and_its_outcomes_are_translated_with_the_coachs_texts(
    client,
    auth_headers,
    fake_translator,  # noqa: F811
):
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        await crud.record_athlete_prediction(
            db,
            user.id,
            prediction="Your 20 min power rises by 5 W within four weeks",
            expected_outcome="A 20 min effort above 255 W",
            horizon="4 weeks",
            category="performance",
            confidence=0.6,
            observed_at=NOW,
        )
        await db.commit()

    before = (await client.get(TRANSLATIONS, headers={**auth_headers, **GERMAN})).json()
    written = (
        await client.post(TRANSLATIONS, headers={**auth_headers, **GERMAN})
    ).json()

    texts = [
        "Your 20 min power rises by 5 W within four weeks",
        "A 20 min effort above 255 W",
    ]
    assert before["missing"] is True
    assert before["missingKey"] == plan_translations.missing_key(texts)
    assert fake_translator == [texts]
    assert written["translations"] == {text: f"DE {text}" for text in texts}
    assert written["missingKey"] is None


@pytest.mark.asyncio
async def test_a_text_left_untranslated_keeps_its_key_until_another_joins_it(
    client, auth_headers, monkeypatch
):
    from services import ai_service

    async def leaves_one_out(texts, language, provider="openai"):
        return {text: f"DE {text}" for text in texts if text != "Stubborn"}

    monkeypatch.setattr(ai_service, "translate_plan_texts", leaves_one_out)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        await crud.record_athlete_open_question(
            db,
            user.id,
            question="Stubborn",
            evidence="Two rides.",
            needs="A third.",
            observed_at=NOW,
        )
        await db.commit()

    first = (await client.post(TRANSLATIONS, headers={**auth_headers, **GERMAN})).json()
    again = (await client.get(TRANSLATIONS, headers={**auth_headers, **GERMAN})).json()

    assert (
        first["missingKey"]
        == again["missingKey"]
        == plan_translations.missing_key(["Stubborn"])
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        await crud.record_athlete_open_question(
            db,
            user.id,
            question="Does heat slow you?",
            evidence="One hot ride.",
            needs="Another.",
            observed_at=NOW,
        )
        await db.commit()

    joined = (await client.get(TRANSLATIONS, headers={**auth_headers, **GERMAN})).json()
    assert joined["missingKey"] not in (None, first["missingKey"])
