"""The texts the training-day guard writes read German without a translation call
(ai-trainer-ops#265).

The guard writes exactly the first days of a new tester's plan. Its texts were
English and waited for the model to translate them, and stayed English when that
failed. They are the app's own fixed words, so their German is fixed too, in
``plan_translations.FIXED``, as the first-day texts of #234 already were.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from services import plan_translations, rest_days
from services.dates import app_today
from tests.test_coach_texts_german_215 import GERMAN, fake_translator  # noqa: F401
from tests.test_rest_days_178 import _days, _stored, _user_days

TODAY = "2026-10-21"  # a Wednesday
WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "saturday",
]  # not Friday, not Sunday


def _guarded() -> list[dict]:
    """A fortnight of sessions for an athlete without rides: Friday and Sunday are no
    training days, and Saturday comes after three days in a row."""
    plan, _reasons = rest_days.guard(
        _days("2026-10-22", 14),
        training_days=WEEKDAYS,
        no_data_yet=True,
        today=TODAY,
        protected=set(),
    )
    return plan


def test_the_guard_writes_both_reasons():
    reasons = {day["description"] for day in _guarded() if rest_days.is_rest(day)}

    assert reasons == {rest_days.OFF_DAY_REASON, rest_days.IN_A_ROW_REASON}


def test_every_text_of_a_guarded_rest_day_has_its_german():
    """Keyed by the exact English text: a reworded one would lose its German."""
    rest = [day for day in _guarded() if rest_days.is_rest(day)]
    german = plan_translations.FIXED["de"]

    # The fortnight's own today: from the real one, its days fall out of the
    # translated window two weeks after they end.
    texts = plan_translations.plan_texts(rest, date.fromisoformat(TODAY))
    assert texts, "the guard wrote no rest day"
    assert [text for text in texts if text not in german] == []


def test_the_german_words():
    german = plan_translations.FIXED["de"]

    assert german[rest_days.REST_TITLE] == "Ruhetag"
    assert (
        german[rest_days.OFF_DAY_REASON]
        == "Keiner deiner Trainingstage, deshalb bleibt er frei."
    )
    assert german[rest_days.IN_A_ROW_REASON] == (
        "Ein Ruhetag nach 3 Trainingstagen in Folge, solange der Plan noch keine Fahrten von dir "
        "hat, nach denen er sich richten kann."
    )
    assert (
        german[rest_days.REST_PURPOSE]
        == "Erholung: Das Training findet an den Tagen davor und danach statt."
    )
    assert [german[text] for text in rest_days.REST_FOCUS] == [
        "Ausruhen oder ein lockerer Spaziergang",
        "Gut schlafen und gut essen",
    ]


@pytest.mark.asyncio
async def test_a_german_account_reads_the_guards_days_in_german_without_a_call(
    client,
    auth_headers,
    mock_ai_service,
    fake_translator,  # noqa: F811
):
    await _user_days(WEEKDAYS)
    first = (app_today() + timedelta(days=1)).isoformat()
    mock_ai_service["generate_training_plan"].return_value = _days(first, 14)
    assert (
        await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    ).status_code == 200
    rest = [day for day in await _stored() if rest_days.is_rest(day)]
    guard_texts = plan_translations.plan_texts(rest, app_today())
    assert rest_days.REST_TITLE in guard_texts

    got = (
        await client.get(
            "/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN}
        )
    ).json()

    german = plan_translations.FIXED["de"]
    assert {text: got["translations"].get(text) for text in guard_texts} == {
        text: german[text] for text in guard_texts
    }
    # The model is asked for its own texts only, never for the guard's.
    await client.post(
        "/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN}
    )
    assert fake_translator and not any(
        text in asked for asked in fake_translator for text in guard_texts
    )
