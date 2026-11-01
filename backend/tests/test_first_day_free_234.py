"""A sign-up on a training day that opens on a rest day says the true reason (ai-trainer-ops#234).

Sam (Mon, Wed, Sat) signed up on a Saturday and read "Rest Day — Take today
completely off the bike to let your body absorb the upcoming training block".
On day one there is nothing to absorb. The owner's (b), 2026-10-24: the rest day
stays, with the true reason and the offer to train today, in fixed words, so the
German is fixed too.
"""

from datetime import timedelta

import pytest

from services import plan_translations, rest_days
from services.dates import app_today
from tests.test_coach_texts_german_215 import GERMAN, fake_translator  # noqa: F401
from tests.test_rest_days_178 import ALL_DAYS, _day, _stored, _user_days

SATURDAY = "2026-10-24"
MONDAY = "2026-10-26"
SAM = ["monday", "wednesday", "saturday"]
ABSORB = _day(
    SATURDAY,
    kind="rest",
    minutes=0,
    description="Take today completely off the bike to let your body absorb the upcoming training block.",
    workoutPurpose="Absorb the upcoming training block.",
    keyFocusPoints=["Stay off the bike"],
)


def _guard(plan, **overrides):
    kwargs = dict(
        training_days=SAM,
        no_data_yet=True,
        today=SATURDAY,
        protected=set(),
        first_plan=True,
    )
    return rest_days.guard(plan, **{**kwargs, **overrides})


def test_the_first_day_says_when_the_first_session_is():
    plan = [ABSORB, _day(MONDAY)]

    guarded, reasons = _guard(plan)

    first = guarded[0]
    assert rest_days.is_rest(first)
    assert first["description"] == (
        "Your plan starts today with a free day; the first session is on Monday. "
        "Want to train today already? Tell your coach."
    )
    assert first["workoutPurpose"] is None and first["keyFocusPoints"] is None
    assert guarded[1] == plan[1]
    assert reasons == {}


def test_without_a_session_ahead_it_says_only_that_the_plan_starts_free():
    guarded, _ = _guard([ABSORB, _day(MONDAY, kind="rest", minutes=0)])

    assert guarded[0]["description"] == rest_days.FIRST_DAY_FREE_NO_SESSION


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_plan": False},  # a plan the athlete is already in
        {"no_data_yet": False},  # with rides, a rest day can have a reason of its own
        {"protected": {SATURDAY}},  # ridden today
    ],
)
def test_any_other_first_day_keeps_its_words(overrides):
    guarded, _ = _guard([ABSORB, _day(MONDAY)], **overrides)

    assert guarded[0]["description"] == ABSORB["description"]


def test_every_first_day_text_has_its_german_text():
    """Keyed by the exact English text, so a reworded one would lose its German."""
    german = plan_translations.FIXED["de"]
    for weekday, tag in zip(rest_days.WEEKDAYS_EN, rest_days.WEEKDAYS_DE):
        assert german[rest_days.FIRST_DAY_FREE.format(weekday=weekday)] == (
            f"Dein Plan startet heute mit einem freien Tag; die erste Einheit ist am {tag}. "
            "Willst du heute schon trainieren? Sag es deinem Coach."
        )
    assert german[rest_days.FIRST_DAY_FREE_NO_SESSION] == (
        "Dein Plan startet heute mit einem freien Tag. Willst du heute schon trainieren? Sag es deinem Coach."
    )


@pytest.mark.asyncio
async def test_the_sign_up_plan_stores_the_reason_and_reads_german_without_a_call(
    client,
    auth_headers,
    mock_ai_service,
    fake_translator,  # noqa: F811
):
    today = app_today()
    await _user_days(ALL_DAYS)
    first_session = today + timedelta(days=1)
    mock_ai_service["generate_training_plan"].return_value = [
        {**ABSORB, "date": today.isoformat()},
        _day(first_session.isoformat()),
    ]

    assert (
        await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    ).status_code == 200

    stored = {day["date"]: day for day in await _stored()}
    text = rest_days.FIRST_DAY_FREE.format(
        weekday=rest_days.WEEKDAYS_EN[first_session.weekday()]
    )
    assert stored[today.isoformat()]["description"] == text
    got = (
        await client.get(
            "/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN}
        )
    ).json()
    assert got["translations"][text] == plan_translations.FIXED["de"][text]
    assert fake_translator == []


def test_a_first_day_that_is_no_training_day_gets_the_true_reason_too():
    """Tom (Mon, Wed, Fri) signed up on a Saturday and read that the rest day
    let his body absorb the previous load (review of 2026-11-01,
    ai-trainer-ops#338 T5)."""
    guarded, _ = _guard(
        [ABSORB, _day(MONDAY)], training_days=["monday", "wednesday", "friday"]
    )

    assert guarded[0]["description"] == rest_days.FIRST_DAY_FREE.format(
        weekday="Monday"
    )
