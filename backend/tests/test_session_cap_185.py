"""A limit on every session, said in chat, holds for the whole plan (ai-trainer-ops#185).

The persona run of 2026-10-21: Mia wrote "Max 1 hour per session, 3 times a
week", the chat shortened one Sunday, and 75- and 105-minute sessions stayed,
because the limit lived in the coach memory only.
"""

from datetime import date, timedelta

import pytest

import crud
from auth import decode_token
from services.availability import SESSION_CAP, constraint_label, extract_availability_constraints
from services.dates import app_today
from services.plan_constraints import describe_constraint_overrides, sanitize_plan_for_constraints
from tests.conftest import TestSessionLocal

TODAY = date(2026, 10, 21)


def _caps(text: str) -> list[int]:
    return [
        c["required_workout"]["maxDurationMinutes"]
        for c in extract_availability_constraints(text, today=TODAY)
        if c["constraint_type"] == SESSION_CAP
    ]


@pytest.mark.parametrize(
    ("text", "caps"),
    [
        ("I have two small kids. Max 1 hour per session, 3 times a week.", [60]),
        ("Ich kann höchstens 45 Minuten pro Einheit.", [45]),
        ("maximal eine Stunde pro Training bitte", [60]),
        ("1 hour max per session", [60]),
        ("no more than an hour per ride", [60]),
        ("max. 90 min pro Ausfahrt", [90]),
        ("Nicht länger als eine halbe Stunde pro Einheit", [30]),
        # A limit with a day named is that day's, not every session's.
        ("Sunday max 1 hour per session", []),
        # A question asks; it limits nothing.
        ("Is max 1 hour per session enough?", []),
        # Without "per session" it may be a day's or a week's.
        ("I can do max 2 hours", []),
        ("max 5 min per session", []),
    ],
)
def test_a_limit_per_session_is_read_from_the_message(text, caps):
    assert _caps(text) == caps


def test_the_limit_is_stored_without_a_date_and_named_in_the_chip():
    [cap] = [c for c in extract_availability_constraints("Max 1 hour per session.", today=TODAY) if c["constraint_type"] == SESSION_CAP]
    assert cap["constraint_date"] is None and cap["expires_on"] is None
    assert constraint_label(SESSION_CAP, None, cap["required_workout"]) == "At most 60 min per session"


CAP = [{"constraintType": SESSION_CAP, "constraintDate": None, "requiredWorkout": {"maxDurationMinutes": 60}}]


def test_every_longer_session_is_shortened_and_says_so():
    plan = [
        {"date": "2026-10-22", "workoutType": "endurance", "title": "Long", "durationMinutes": 75,
         "description": "Ride for 75 minutes at 115 to 145 watts. Include 4 x 8 min at tempo."},
        {"date": "2026-10-25", "workoutType": "endurance", "title": "Window", "durationMinutes": 105,
         "durationMinMinutes": 90, "durationMaxMinutes": 120, "description": "Ride for 90–120 min, steady."},
        {"date": "2026-10-26", "workoutType": "endurance", "title": "Short", "durationMinutes": 45,
         "description": "Ride for 45 minutes."},
        {"date": "2026-10-27", "workoutType": "rest", "title": "Rest", "durationMinutes": 0, "description": "Rest."},
    ]

    long_day, window_day, short_day, rest_day = sanitize_plan_for_constraints(plan, CAP)

    assert long_day["durationMinutes"] == 60
    assert long_day["description"] == "Ride for 60 min at 115 to 145 watts. Include 4 x 8 min at tempo."
    assert window_day["durationMinutes"] == 60
    assert window_day.get("durationMinMinutes") is None and window_day.get("durationMaxMinutes") is None
    assert window_day["description"] == "Ride for 60 min, steady."
    assert (short_day["durationMinutes"], short_day["description"]) == (45, "Ride for 45 minutes.")
    assert rest_day["durationMinutes"] == 0


def test_a_long_ride_the_athlete_pinned_to_a_date_keeps_its_length():
    pinned = CAP + [{"constraintType": "required_workout", "constraintDate": "2026-10-25",
                     "requiredWorkout": {"workoutType": "endurance", "minDurationMinutes": 120}}]
    [day] = sanitize_plan_for_constraints(
        [{"date": "2026-10-25", "workoutType": "endurance", "title": "Long", "durationMinutes": 150, "description": "Long."}],
        pinned,
    )
    assert day["durationMinutes"] == 150


def test_a_longer_session_the_coach_asks_for_is_reported():
    [override] = describe_constraint_overrides(
        [{"date": "2026-10-29", "workoutType": "endurance", "durationMinutes": 90}], CAP
    )
    assert override == {"date": "2026-10-29", "weekday": "thursday", "constraintType": SESSION_CAP,
                        "requiredType": None, "capMinutes": 60}


@pytest.mark.asyncio
async def test_saying_the_limit_in_chat_shortens_the_stored_plan(client, auth_headers, mock_ai_service):
    today = app_today()
    soon, later, short = (str(today + timedelta(days=n)) for n in (1, 4, 5))
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [
            {"date": soon, "workoutType": "endurance", "title": "Ride", "durationMinutes": 75,
             "description": "Ride for 75 minutes."},
            {"date": later, "workoutType": "endurance", "title": "Long", "durationMinutes": 105,
             "durationMinMinutes": 90, "durationMaxMinutes": 120, "description": "Ride for 90–120 min."},
            {"date": short, "workoutType": "endurance", "title": "Short", "durationMinutes": 45,
             "description": "Ride for 45 minutes."},
        ])
        await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I have two small kids. Max 1 hour per session, 3 times a week."},
    )

    body = response.json()
    assert [c["label"] for c in body["capturedConstraints"]] == ["At most 60 min per session"]
    assert "so I shortened" in body["response"] and soon in body["response"] and later in body["response"]
    async with TestSessionLocal() as db:
        stored = {d["date"]: d for d in (await crud.get_training_plan(db, user_id)).plan}
    assert [stored[d]["durationMinutes"] for d in (soon, later, short)] == [60, 60, 45]
    assert stored[soon]["description"] == "Ride for 60 min."
