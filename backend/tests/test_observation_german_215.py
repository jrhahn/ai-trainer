"""The observations the chat's signal rules store read German without a call
(ai-trainer-ops#215).

Settings shows the athlete what the coach observed about them. The twelve
observations of workout_curiosity are the app's own fixed words, so their German
is fixed too, in ``plan_translations.FIXED``, as for the starting hypotheses.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
from services import plan_translations, workout_curiosity
from tests.conftest import TestSessionLocal
from tests.test_coach_texts_german_215 import GERMAN, _rider_id, fake_translator  # noqa: F401

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)


def test_every_observation_has_its_german():
    """Keyed by the exact English text: a reworded observation would lose its German."""
    observations = {rule.observation for rule in workout_curiosity.SIGNAL_RULES}

    assert len(observations) == len(workout_curiosity.SIGNAL_RULES) == 12
    assert set(workout_curiosity.GERMAN) == observations
    assert all(
        plan_translations.FIXED["de"][text] == workout_curiosity.GERMAN[text]
        for text in observations
    )
    assert all(
        german.startswith(("Du ", "Dich ", "Die Qualität deiner", "Deine "))
        for german in workout_curiosity.GERMAN.values()
    )


@pytest.mark.asyncio
async def test_a_stored_observation_reads_german_without_a_call(
    client,
    auth_headers,
    fake_translator,  # noqa: F811
):
    observation = next(
        r.observation
        for r in workout_curiosity.SIGNAL_RULES
        if r.name == "slept_or_stressed"
    )
    async with TestSessionLocal() as db:
        await crud.observe_athlete_memory_fact(
            db,
            await _rider_id(db),
            fact=observation,
            category=workout_curiosity.MEMORY_CATEGORY,
            observed_at=NOW,
        )
        await db.commit()

    got = (
        await client.get(
            "/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN}
        )
    ).json()

    assert (
        got["translations"][observation]
        == "Die Qualität deiner Einheiten hängt von Schlaf und Alltagsstress ab"
    )
    assert got["missing"] is False
    assert fake_translator == []
