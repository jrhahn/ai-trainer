"""A weekday limit said in chat holds for the next such day only, unless the
athlete says it holds for every one (ai-trainer-ops#295).

The owner decided on 2026-10-24: "Am Samstag kann ich nur 2 Stunden" is this
Saturday's limit. "immer", "generell", the adverb "samstags", "every Saturday"
and the like make it every Saturday's, as #231 stored every weekday limit.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from services.availability import (
    SESSION_CAP,
    constraint_label,
    extract_availability_constraints,
)
from services.dates import app_today
from services.plan_constraints import (
    day_session_cap,
    sanitize_plan_for_constraints,
    session_cap_is_that_days_only,
)
from tests.conftest import notes_text
from tests.test_session_cap_185 import _ask, _seed, _stored

THURSDAY = date(2026, 10, 22)
SATURDAY = date(2026, 10, 24)


def _caps(text: str, today: date = THURSDAY) -> list[tuple[str, str | None, int]]:
    """(weekday, the one date it holds on or None for every such day, minutes)."""
    return [
        (
            c["weekday"],
            c["constraint_date"],
            c["required_workout"]["maxDurationMinutes"],
        )
        for c in extract_availability_constraints(text, today=today)
        if c["constraint_type"] == SESSION_CAP and c["weekday"]
    ]


@pytest.mark.parametrize(
    ("text", "caps"),
    [
        # The next such day only.
        ("Am Samstag kann ich nur 2 Stunden.", [("saturday", "2026-10-24", 120)]),
        ("Diesen Samstag habe ich nur 2 Stunden.", [("saturday", "2026-10-24", 120)]),
        ("This Saturday I only have 2 hours.", [("saturday", "2026-10-24", 120)]),
        ("I only have 2 hours on Saturday.", [("saturday", "2026-10-24", 120)]),
        ("Saturday max 2 hours", [("saturday", "2026-10-24", 120)]),
        (
            "Dienstag und Donnerstag habe ich nur eine Stunde.",
            [("tuesday", "2026-10-27", 60), ("thursday", "2026-10-22", 60)],
        ),
        # Every such day: a word that says so, or the adverb and the plural.
        ("Samstags kann ich immer nur 2 Stunden.", [("saturday", None, 120)]),
        ("Generell habe ich samstags nur 2 Stunden.", [("saturday", None, 120)]),
        ("Am Samstag habe ich immer nur 2 Stunden.", [("saturday", None, 120)]),
        ("Jeden Samstag habe ich nur 2 Stunden.", [("saturday", None, 120)]),
        ("Grundsätzlich kann ich am Samstag nur 2 Stunden.", [("saturday", None, 120)]),
        ("Mittwochs kann ich höchstens 90 Minuten.", [("wednesday", None, 90)]),
        ("An Samstagen habe ich nur 2 Stunden.", [("saturday", None, 120)]),
        (
            "Dienstags und donnerstags habe ich immer nur eine Stunde.",
            [("tuesday", None, 60), ("thursday", None, 60)],
        ),
        ("I only ever have 2 hours on Saturdays.", [("saturday", None, 120)]),
        ("On Saturdays I only have 2 hours.", [("saturday", None, 120)]),
        ("Every Saturday I only have 2 hours.", [("saturday", None, 120)]),
        ("I usually only have 2 hours on Saturday.", [("saturday", None, 120)]),
    ],
)
def test_the_next_such_day_unless_said_for_every_one(text, caps):
    assert _caps(text) == caps


def test_next_saturday_said_on_a_saturday_is_a_week_away():
    assert _caps("Nächsten Samstag kann ich nur 2 Stunden.", today=SATURDAY) == [
        ("saturday", "2026-10-31", 120)
    ]
    assert _caps("Diesen Samstag kann ich nur 2 Stunden.", today=SATURDAY) == [
        ("saturday", "2026-10-24", 120)
    ]


def test_a_limit_for_one_day_expires_with_it_and_its_chip_says_so():
    [cap] = [
        c
        for c in extract_availability_constraints(
            "Am Samstag kann ich nur 2 Stunden.", today=THURSDAY
        )
        if c["constraint_type"] == SESSION_CAP
    ]
    assert cap["expires_on"] == "2026-10-24"
    assert constraint_label(
        SESSION_CAP, cap["constraint_date"], cap["required_workout"], cap["weekday"]
    ) == ("At most 120 min on Saturday 2026-10-24 only")


def _cap(minutes: int, weekday: str, on: str | None) -> dict:
    return {
        "constraintType": SESSION_CAP,
        "constraintDate": on,
        "weekday": weekday,
        "requiredWorkout": {
            "maxDurationMinutes": minutes,
            "since": str(app_today() - timedelta(days=1)),
        },
    }


def _saturdays() -> tuple[dict, dict]:
    first = app_today() + timedelta(days=(5 - app_today().weekday()) % 7 or 7)
    return tuple(
        {
            "date": str(day),
            "workoutType": "endurance",
            "title": "Ride",
            "durationMinutes": 180,
            "description": "Ride for 180 minutes.",
        }
        for day in (first, first + timedelta(days=7))
    )


def test_a_limit_for_one_saturday_leaves_the_saturday_after_alone():
    this, next_week = _saturdays()

    once = sanitize_plan_for_constraints(
        [this, next_week], [_cap(120, "saturday", this["date"])]
    )
    every = sanitize_plan_for_constraints(
        [this, next_week], [_cap(120, "saturday", None)]
    )

    assert [d["durationMinutes"] for d in once] == [120, 180]
    assert [d["durationMinutes"] for d in every] == [120, 120]
    assert day_session_cap(next_week, [_cap(120, "saturday", this["date"])]) is None


@pytest.mark.asyncio
async def test_the_chat_shortens_the_next_saturday_only_and_says_so(
    client, auth_headers, mock_ai_service
):
    this, next_week = _saturdays()
    user_id = await _seed(auth_headers, [this, next_week])

    body = await _ask(client, auth_headers, "Am Samstag kann ich nur 2 Stunden.")

    stored = await _stored(user_id)
    assert [c["label"] for c in body["capturedConstraints"]] == [
        f"At most 120 min on Saturday {this['date']} only"
    ]
    assert (
        stored[this["date"]]["durationMinutes"],
        stored[next_week["date"]]["durationMinutes"],
    ) == (120, 180)
    day, month = this["date"][8:10], this["date"][5:7]
    assert f"Nur am Samstag, {day}.{month}. hast du höchstens 120 min" in notes_text(
        body
    )


@pytest.mark.asyncio
async def test_the_chat_shortens_every_saturday_when_told_so(
    client, auth_headers, mock_ai_service
):
    this, next_week = _saturdays()
    user_id = await _seed(auth_headers, [this, next_week])

    body = await _ask(client, auth_headers, "Samstags kann ich immer nur 2 Stunden.")

    stored = await _stored(user_id)
    assert [c["label"] for c in body["capturedConstraints"]] == [
        "At most 120 min on Saturdays"
    ]
    assert (
        stored[this["date"]]["durationMinutes"],
        stored[next_week["date"]]["durationMinutes"],
    ) == (120, 120)
    assert "Samstags hast du höchstens 120 min" in notes_text(body)


@pytest.mark.parametrize(
    ("caps", "only_that_day"),
    [
        ([("saturday", "on", 120)], True),
        # A standing limit as short or shorter holds there too: it is every Saturday's
        # (review of jrhahn/ai-trainer#1120).
        ([("saturday", "on", 120), ("saturday", None, 120)], False),
        ([("saturday", "on", 120), ("saturday", None, 90)], False),
        ([("saturday", "on", 120), (None, None, 90)], False),
        ([("saturday", "on", 120), ("saturday", None, 150)], True),
        ([("saturday", None, 120)], False),
    ],
)
def test_the_note_says_that_day_only_when_no_standing_limit_is_as_short(
    caps, only_that_day
):
    this, _next = _saturdays()
    constraints = [_cap(m, w, this["date"] if on else None) for w, on, m in caps]

    assert session_cap_is_that_days_only(this, constraints) is only_that_day


def test_a_weekday_named_twice_in_one_part_is_one_limit():
    assert _caps("Samstag und samstags habe ich nur 2 Stunden.") == [
        ("saturday", None, 120)
    ]


@pytest.mark.asyncio
async def test_the_english_note_names_the_one_day(
    client, auth_headers, mock_ai_service
):
    this, next_week = _saturdays()
    await _seed(auth_headers, [this, next_week])

    body = await _ask(client, auth_headers, "This Saturday I only have 2 hours.")

    assert (
        f"Only on Saturday {this['date']} you have at most 120 min, so I shortened that session."
        in notes_text(body)
    )
