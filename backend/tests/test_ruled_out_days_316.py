"""A day the athlete rules out in chat is stored, and freed in the plan
(ai-trainer-ops#316).

On 2026-10-25 an athlete wrote "Montag und Donnerstag gehen nicht". The coach
freed Monday, and Thursday kept its strength session; asked again, it stayed.
The phrasing stored no constraint at all, so nothing but the model could free
the day, and a constraint once stored only took effect on the next write.
"""

from __future__ import annotations

from datetime import date

import pytest

import schemas
from domain.availability import extract_availability_constraints
from domain.plan_day import planned_minutes
from services import interference
from services.dates import app_today
from tests.conftest import notes_text
from tests.test_session_cap_185 import _ask, _seed, _stored

SUNDAY = date(2026, 10, 25)


def _blocked(text: str, today: date = SUNDAY) -> list[str]:
    return sorted(
        c["constraint_date"]
        for c in extract_availability_constraints(text, today=today)
        if c["constraint_type"] == "no_training"
    )


MONDAY, TUESDAY, THURSDAY, FRIDAY = (
    "2026-10-26",
    "2026-10-27",
    "2026-10-29",
    "2026-10-30",
)


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        ("Montag und Donnerstag gehen nicht", [MONDAY, THURSDAY]),
        (
            "Montag und Donnerstag gehen bei mir diese Woche übrigens nicht, sonst passt alles",
            [MONDAY, THURSDAY],
        ),
        ("Am Montag und am Donnerstag kann ich nicht trainieren", [MONDAY, THURSDAY]),
        ("Montag und Donnerstag habe ich keine Zeit", [MONDAY, THURSDAY]),
        ("Donnerstag geht nicht", [THURSDAY]),
        ("Donnerstag passt leider nicht", [THURSDAY]),
        ("Donnerstag fällt aus", [THURSDAY]),
        ("Donnerstag ist raus", [THURSDAY]),
        ("Donnerstag schaffe ich nicht", [THURSDAY]),
        ("Freitag bin ich auf einer Hochzeit, da wird nichts mit Training", [FRIDAY]),
        ("Thursday is out", [THURSDAY]),
        ("Thursday does not work for me", [THURSDAY]),
        ("Tuesday and Friday don't work for me, I'm travelling", [TUESDAY, FRIDAY]),
    ],
)
def test_a_day_ruled_out_in_passing_is_stored(text, dates):
    assert _blocked(text) == dates


@pytest.mark.parametrize(
    "text",
    [
        # How the day goes, not whether.
        "Donnerstag geht nicht so gut",
        "Montag geht es mir nicht gut",
        # Something else is out.
        "Morgen ist das Rad raus aus der Werkstatt",
        "Montag habe ich keine Zeit zum Kochen, aber Zeit zum Fahren",
        # A question, and a limit on how long.
        "Passt Donnerstag nicht?",
        "Donnerstag geht nicht so lange, nur 1 Stunde",
        "The plan for Thursday works for me",
    ],
)
def test_other_sentences_rule_no_day_out(text):
    assert _blocked(text) == []


def _session(day: str, **fields) -> dict:
    return {
        "date": day,
        "workoutType": "endurance",
        "title": "Ride",
        "description": "Ride for 90 minutes.",
        "durationMinutes": 90,
        **fields,
    }


def _strength(day: str) -> dict:
    return _session(
        day,
        sport="strength",
        workoutType="strength",
        title="Kraft Ganzkörper",
        description="45 Minuten Kraft.",
        durationMinutes=45,
        strengthExercises=[{"exercise": "back squat", "sets": 3, "reps": 5, "rir": 2}],
    )


def _rest(day: str) -> dict:
    return {
        "date": day,
        "workoutType": "rest",
        "title": "Rest Day",
        "description": "Rest",
        "durationMinutes": 0,
    }


QUESTION = "Montag und Donnerstag gehen nicht."


def _days() -> dict[str, str]:
    """The Monday and the Thursday the question names, from today."""
    return {
        c["weekday"]: c["constraint_date"]
        for c in extract_availability_constraints(QUESTION, today=app_today())
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coach_frees", "noted"),
    [
        # The case of 2026-10-25: Monday freed, Thursday's strength kept.
        (["monday"], ["thursday"]),
        # The coach freed nothing.
        ([], ["monday", "thursday"]),
        # The coach freed both: nothing for the app to say.
        (["monday", "thursday"], []),
    ],
)
async def test_a_day_ruled_out_in_chat_is_free_whatever_the_coach_sent(
    client, auth_headers, mock_ai_service, coach_frees, noted
):
    days = _days()
    user_id = await _seed(
        auth_headers, [_session(days["monday"]), _strength(days["thursday"])]
    )
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Alles klar, das habe ich mir notiert.",
        "plan_updates": [_rest(days[weekday]) for weekday in coach_frees],
        "sources": [],
    }

    body = await _ask(client, auth_headers, QUESTION)

    stored = await _stored(user_id)
    assert [planned_minutes(stored[days[w]]) for w in ("monday", "thursday")] == [0, 0]
    # The freed day lists no squats and names no gym.
    thursday = stored[days["thursday"]]
    assert "strengthExercises" not in thursday
    assert thursday.get("sport") != "strength"
    said = notes_text(body)
    assert [
        w
        for w, german in (("monday", "Montag"), ("thursday", "Donnerstag"))
        if german in said
    ] == noted


def test_a_rest_day_keeps_nothing_of_the_session_it_replaced():
    rested = schemas.canonical_plan_day(
        {
            **_strength(THURSDAY),
            "workoutType": "rest",
            "durationMinutes": 0,
            "intervals": [{"duration": 120, "power": 300, "rest": 120}],
            "targetPower": {"low": 200, "high": 250},
        }
    )

    assert {
        "strengthExercises",
        "intervals",
        "targetPower",
        "sport",
    } & rested.keys() == set()


def test_a_runners_rest_day_stays_a_runners():
    rested = schemas.canonical_plan_day(_rest(THURSDAY) | {"sport": "running"})

    assert rested["sport"] == "running"


def test_a_rest_day_is_no_gym_work_for_the_interference_guard():
    rested = _rest(THURSDAY) | {"sport": "strength"}

    assert not interference.is_strength_session(rested)
    assert interference.is_strength_session(_strength(THURSDAY))
