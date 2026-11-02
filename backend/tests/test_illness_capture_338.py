"""An illness said in other words, or as past, is captured (review of
2026-11-01, training A4, ai-trainer-ops#338).

The comeback's first message, "I was ill last week, can I train again?",
captured nothing: no return prior, no writer guard. Nor did a positive test, a
fever in degrees, a chest infection or a sore throat.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from domain.athlete_states import (
    KIND_FEVER,
    KIND_ILL,
    StatedState,
    extract_states,
    return_days,
    says_not_ill,
)

MONDAY = date(2026, 11, 2)
YESTERDAY = MONDAY - timedelta(days=1)


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("Corona positiv seit Dienstag.", KIND_FEVER),
        ("I tested positive for covid yesterday.", KIND_FEVER),
        ("Ich bin positiv auf Corona getestet worden.", KIND_FEVER),
        ("Ich bin corona-positiv.", KIND_FEVER),
        ("I've got a chest infection.", KIND_FEVER),
        ("Ich liege mit 39 Grad im Bett.", KIND_FEVER),
        ("Seit heute 38,5° Fieber.", KIND_FEVER),
        ("I've had a fever of 39 since this morning.", KIND_FEVER),
        ("Ich habe Bronchitis.", KIND_FEVER),
        ("Ich habe einen grippalen Infekt.", KIND_ILL),
        # Above the neck: ill, not fever (the "neck check").
        ("My throat is sore and I have a runny nose.", KIND_ILL),
        ("I have a sore throat.", KIND_ILL),
        ("Ich habe Schnupfen und Halsweh.", KIND_ILL),
        ("Mir tut der Hals weh.", KIND_ILL),
        ("Ich habe Husten.", KIND_ILL),
        ("I have a bad cold.", KIND_ILL),
        # A greeting first: each sentence is read on its own (review of #1284).
        ("Hallo! Corona positiv seit Dienstag.", KIND_FEVER),
    ],
)
def test_an_illness_in_other_words_is_captured(message, kind):
    assert extract_states(message, MONDAY) == [StatedState(kind, MONDAY, None)]


@pytest.mark.parametrize(
    ("message", "kind", "days"),
    [
        ("I had the flu last week.", KIND_FEVER, 1),
        (
            "I was sick with the flu all last week, now I feel fine.",
            KIND_FEVER,
            7,
        ),
        ("Ich war eine Woche krank.", KIND_ILL, 7),
        ("Ich habe letzte Woche Grippe gehabt.", KIND_FEVER, 1),
        ("Ich hatte Corona, 10 Tage lang.", KIND_FEVER, 10),
        ("I had a bad cold last week.", KIND_ILL, 1),
        ("Ich war 3 Tage krank.", KIND_ILL, 3),
        # The "ich" left out.
        ("War letzte Woche krank, jetzt geht's wieder.", KIND_ILL, 1),
        # The athlete's own beside someone else's.
        ("My son was sick and I had a fever.", KIND_FEVER, 1),
    ],
)
def test_a_recent_past_illness_is_captured_as_just_over(message, kind, days):
    assert extract_states(message, MONDAY) == [
        StatedState(kind, YESTERDAY - timedelta(days=days - 1), YESTERDAY)
    ]


def test_a_week_of_flu_asks_for_a_week_of_easy_days():
    [state] = extract_states(
        "I was sick with the flu all last week, now I feel fine. Can I do intervals?",
        MONDAY,
    )

    assert return_days(state.kind, state.starts_on, state.ends_on) == 7


@pytest.mark.parametrize(
    "message",
    [
        "Mein Sohn war letzte Woche krank.",
        "My wife tested positive.",
        "Ich war nicht krank.",
        "Ich hatte keine Grippe.",
        "Ich habe keine Grippe gehabt.",
        "I had covid in 2021.",
        "Letztes Jahr hatte ich Corona.",
        "Ich hatte vor ein paar Monaten Grippe.",
        "Als Kind war ich oft krank.",
        "I was sick of intervals.",
        "I had a cold shower after the ride.",
        "I had the flu shot last week.",
        "Wenn ich krank war, fahre ich langsam.",
        "Morgen 38 Grad, ich fahre früh.",
        "Ich bin positiv eingestellt.",
        # Slang and "ill-prepared" (review of #1284).
        "That climb was sick!",
        "The descent was so sick.",
        "I was ill-prepared for the race.",
        "I tested positive for covid in 2021.",
        "If I tested positive, I would stay home.",
        "I have no fever.",
        "Ich habe keinen Husten.",
        "I have to cough on climbs.",
        "Ich habe Hustensaft gekauft.",
    ],
)
def test_no_illness_of_the_athletes_own_now_or_just_over(message):
    assert extract_states(message, MONDAY) == []


def test_a_symptom_keeps_a_take_back_from_deleting_the_illness():
    assert not says_not_ill("I'm not sick, but I have a runny nose.")


@pytest.mark.asyncio
async def test_a_past_flu_is_stored_and_named_as_over_with_easy_days(
    client, auth_headers, mock_ai_service
):
    from services.dates import app_today
    from tests.test_red_flags_338 import _ask, _states

    body = await _ask(
        client,
        auth_headers,
        "I was sick with the flu all last week, now I feel fine. Can I do intervals?",
    )

    today = app_today()
    yesterday = today - timedelta(days=1)
    [state] = await _states(auth_headers)
    assert (state.kind, state.ends_on) == (KIND_FEVER, yesterday)
    [chip] = body["capturedStates"]
    # A week of flu: a week of easy days, and no "no training" for an illness
    # that is over.
    easy_until = yesterday + timedelta(days=7)
    assert chip["label"] == f"Well again: easy days until {easy_until.isoformat()}"
