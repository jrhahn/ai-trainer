"""A limit on every session, said in chat, holds for the whole plan (ai-trainer-ops#185).

The persona run of 2026-10-21: Mia wrote "Max 1 hour per session, 3 times a
week", the chat shortened one Sunday, and 75- and 105-minute sessions stayed,
because the limit lived in the coach memory only.
"""

from datetime import date, timedelta

import pytest

import crud
from auth import decode_token
from services.availability import SESSION_CAP, WEEKDAY_LABELS, constraint_label, extract_availability_constraints
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
    assert cap["required_workout"] == {"maxDurationMinutes": 60, "since": "2026-10-21"}
    assert constraint_label(SESSION_CAP, None, cap["required_workout"]) == "At most 60 min per session"


def _on(offset: int) -> str:
    """A date relative to the app's today, so the tests do not age."""
    return str(app_today() + timedelta(days=offset))


def _cap(since_offset: int = 0) -> list[dict]:
    return [{"constraintType": SESSION_CAP, "constraintDate": None,
             "requiredWorkout": {"maxDurationMinutes": 60, "since": _on(since_offset)}}]


CAP = _cap()


def test_a_session_before_the_limit_was_said_keeps_its_length():
    """History stays: coach_chat writes skip the completed-day guard (review of #1000)."""
    [past] = sanitize_plan_for_constraints(
        [{"date": _on(-1), "workoutType": "endurance", "title": "Ridden", "durationMinutes": 90,
          "completed": True, "description": "Ride for 90 minutes."}],
        CAP,
    )
    assert (past["durationMinutes"], past["description"]) == (90, "Ride for 90 minutes.")


def test_a_session_ridden_after_the_limit_was_said_keeps_its_length_too():
    """Said three days ago: the days since then are past now, ridden or not (review 2 of #1000)."""
    ridden, missed, completed_today = sanitize_plan_for_constraints(
        [
            {"date": _on(-1), "workoutType": "endurance", "title": "Ridden", "durationMinutes": 90,
             "completed": True, "description": "Ride for 90 minutes."},
            {"date": _on(-2), "workoutType": "endurance", "title": "Missed", "durationMinutes": 90,
             "description": "Ride for 90 minutes."},
            {"date": _on(0), "workoutType": "endurance", "title": "This morning", "durationMinutes": 90,
             "completed": True, "description": "Ride for 90 minutes."},
        ],
        _cap(since_offset=-3),
    )
    assert [d["durationMinutes"] for d in (ridden, missed, completed_today)] == [90, 90, 90]


def test_every_longer_session_is_shortened_and_says_so():
    plan = [
        {"date": _on(1), "workoutType": "endurance", "title": "Long", "durationMinutes": 75,
         "description": "Ride for 75 minutes at 115 to 145 watts. Include 4 x 8 min at tempo."},
        {"date": _on(4), "workoutType": "endurance", "title": "Window", "durationMinutes": 105,
         "durationMinMinutes": 90, "durationMaxMinutes": 120, "description": "Ride for 90–120 min, steady."},
        {"date": _on(5), "workoutType": "endurance", "title": "Short", "durationMinutes": 45,
         "description": "Ride for 45 minutes."},
        {"date": _on(6), "workoutType": "rest", "title": "Rest", "durationMinutes": 0, "description": "Rest."},
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
    pinned = CAP + [{"constraintType": "required_workout", "constraintDate": _on(4),
                     "requiredWorkout": {"workoutType": "endurance", "minDurationMinutes": 120}}]
    [day] = sanitize_plan_for_constraints(
        [{"date": _on(4), "workoutType": "endurance", "title": "Long", "durationMinutes": 150, "description": "Long."}],
        pinned,
    )
    assert day["durationMinutes"] == 150


def test_a_longer_session_the_coach_asks_for_is_reported():
    when = _on(8)
    [override] = describe_constraint_overrides(
        [{"date": when, "workoutType": "endurance", "durationMinutes": 90}], CAP
    )
    assert override == {"date": when, "weekday": WEEKDAY_LABELS[date.fromisoformat(when).weekday()],
                        "constraintType": SESSION_CAP, "requiredType": None, "capMinutes": 60}


def _day(offset: int, minutes: int, **extra) -> dict:
    return {"date": str(app_today() + timedelta(days=offset)), "workoutType": "endurance", "title": "Ride",
            "durationMinutes": minutes, "description": f"Ride for {minutes} minutes.", **extra}


async def _seed(auth_headers, days: list[dict]) -> str:
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, days)
        await db.commit()
    return user_id


async def _stored(user_id: str) -> dict[str, dict]:
    async with TestSessionLocal() as db:
        return {d["date"]: d for d in (await crud.get_training_plan(db, user_id)).plan}


async def _ask(client, auth_headers, question: str) -> dict:
    response = await client.post("/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": question})
    return response.json()


@pytest.mark.asyncio
async def test_saying_the_limit_in_chat_shortens_the_stored_plan(client, auth_headers, mock_ai_service):
    ridden, soon, later, short = _day(-1, 90, completed=True), _day(1, 75), _day(4, 105), _day(5, 45)
    later.update(durationMinMinutes=90, durationMaxMinutes=120, description="Ride for 90–120 min.")
    user_id = await _seed(auth_headers, [ridden, soon, later, short])

    body = await _ask(client, auth_headers, "I have two small kids. Max 1 hour per session, 3 times a week.")

    assert [c["label"] for c in body["capturedConstraints"]] == ["At most 60 min per session"]
    assert "so I shortened" in body["response"]
    assert soon["date"] in body["response"] and later["date"] in body["response"]
    assert ridden["date"] not in body["response"]
    stored = await _stored(user_id)
    assert [stored[d["date"]]["durationMinutes"] for d in (ridden, soon, later, short)] == [90, 60, 60, 45]
    assert stored[soon["date"]]["description"] == "Ride for 60 min."


@pytest.mark.asyncio
async def test_the_note_is_german_for_a_german_message(client, auth_headers, mock_ai_service):
    await _seed(auth_headers, [_day(2, 75)])

    body = await _ask(client, auth_headers, "Ich habe zwei kleine Kinder. Maximal eine Stunde pro Einheit.")

    assert "Hinweis: Deine Grenze von 60 min pro Einheit gilt jetzt für jede Einheit" in body["response"]


@pytest.mark.asyncio
async def test_a_longer_session_the_coach_plans_is_kept_to_the_limit_and_said(client, auth_headers, mock_ai_service):
    target = _day(2, 45)
    user_id = await _seed(auth_headers, [target])
    mock_ai_service["ask_trainer"].return_value = {
        **mock_ai_service["ask_trainer"].return_value,
        "response": "Let's make Saturday a long one.",
        "plan_updates": [{**target, "title": "Long ride", "durationMinutes": 120, "description": "Ride for 120 minutes."}],
    }

    body = await _ask(client, auth_headers, "Max 1 hour per session please. And a long ride on the weekend?")

    assert "to 60 min: that is your limit per session." in body["response"]
    assert (await _stored(user_id))[target["date"]]["durationMinutes"] == 60


@pytest.mark.asyncio
async def test_a_new_limit_replaces_the_old_one(client, auth_headers, mock_ai_service):
    user_id = await _seed(auth_headers, [_day(2, 45)])

    await _ask(client, auth_headers, "Max 1 hour per session.")
    await _ask(client, auth_headers, "Good news: now max 2 hours per session.")

    async with TestSessionLocal() as db:
        active = await crud.list_active_availability_constraints(db, user_id, today=app_today().isoformat())
    assert [c.required_workout["maxDurationMinutes"] for c in active if c.constraint_type == SESSION_CAP] == [120]
