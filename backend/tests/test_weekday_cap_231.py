"""A limit on named weekdays, said in chat, holds for those days (ai-trainer-ops#231).

Lena, message 1 of the persona run of 2026-10-24: "Dienstag und Donnerstag
habe ich nur eine Stunde, am Wochenende gerne 3 bis 4 Stunden." The coach said
"gut zu wissen", stored it in its memory, and her Tuesday stayed at 75 minutes.
"Max 1 hour per session" became a limit at once (#185); this is the same limit
for the weekdays the athlete names.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from domain.availability import (
    SESSION_CAP,
    constraint_label,
    extract_availability_constraints,
)
from services import coach_chat
from services.dates import app_today
from services.plan_constraints import (
    describe_constraint_overrides,
    sanitize_plan_for_constraints,
)
from tests.test_session_cap_185 import _ask, _day, _seed, _stored
from tests.conftest import notes_text

TODAY = date(2026, 10, 24)
LENA = (
    "Ich habe einen Powermeter und fahre gerade etwa 8 Stunden pro Woche. Dienstag und "
    "Donnerstag habe ich nur eine Stunde, am Wochenende gerne 3 bis 4 Stunden."
)
LENA_ALWAYS = (
    "Ich habe einen Powermeter und fahre gerade etwa 8 Stunden pro Woche. Dienstags und "
    "donnerstags habe ich immer nur eine Stunde, am Wochenende gerne 3 bis 4 Stunden."
)


def _weekday_caps(text: str) -> list[tuple[str, int]]:
    return [
        (c["weekday"], c["required_workout"]["maxDurationMinutes"])
        for c in extract_availability_constraints(text, today=TODAY)
        if c["constraint_type"] == SESSION_CAP and c["weekday"]
    ]


@pytest.mark.parametrize(
    ("text", "caps"),
    [
        (LENA, [("tuesday", 60), ("thursday", 60)]),
        ("On Tuesdays I only have 45 minutes.", [("tuesday", 45)]),
        ("Mittwochs kann ich höchstens 90 Minuten.", [("wednesday", 90)]),
        ("Sunday max 1 hour per session", [("sunday", 60)]),
        ("Freitag max. 1,5 Stunden", [("friday", 90)]),
        # A wish for one session names no limit, and neither does a day off.
        ("Dienstag nur eine Stunde locker", []),
        ("Samstag habe ich keine Zeit.", []),
        ("Max 1 hour per session.", []),
        # A question asks.
        ("Habe ich dienstags nur eine Stunde?", []),
    ],
)
def test_a_limit_for_named_weekdays_is_read_from_the_message(text, caps):
    assert _weekday_caps(text) == caps


def test_the_chip_names_the_weekday():
    assert (
        constraint_label(SESSION_CAP, None, {"maxDurationMinutes": 60}, "tuesday")
        == "At most 60 min on Tuesdays"
    )
    assert (
        constraint_label(SESSION_CAP, None, {"maxDurationMinutes": 60})
        == "At most 60 min per session"
    )


def _on_weekday(weekday: int, minutes: int) -> dict:
    offset = (weekday - app_today().weekday()) % 7 or 7
    return _day(offset, minutes)


def _cap(minutes: int, weekday: str | None = None) -> dict:
    return {
        "constraintType": SESSION_CAP,
        "constraintDate": None,
        "weekday": weekday,
        "requiredWorkout": {
            "maxDurationMinutes": minutes,
            "since": str(app_today() - timedelta(days=1)),
        },
    }


def test_only_the_named_weekday_is_shortened_and_the_shorter_limit_holds():
    tuesday, wednesday, thursday = (
        _on_weekday(1, 75),
        _on_weekday(2, 75),
        _on_weekday(3, 120),
    )
    constraints = [_cap(60, "tuesday"), _cap(90)]

    tue, wed, thu = sanitize_plan_for_constraints(
        [tuesday, wednesday, thursday], constraints
    )

    assert (tue["durationMinutes"], tue["description"]) == (60, "Ride for 60 min.")
    assert (wed["durationMinutes"], wed["description"]) == (75, "Ride for 75 minutes.")
    assert (thu["durationMinutes"], thu["description"]) == (90, "Ride for 90 min.")
    [override] = describe_constraint_overrides(
        [{**tuesday, "durationMinutes": 70}], constraints
    )
    assert override["capMinutes"] == 60


def test_a_weekday_limit_longer_than_every_sessions_does_not_lift_it():
    [saturday] = sanitize_plan_for_constraints(
        [_on_weekday(5, 110)], [_cap(120, "saturday"), _cap(90)]
    )

    assert saturday["durationMinutes"] == 90


@pytest.mark.asyncio
async def test_lenas_message_shortens_her_tuesday_and_thursday_and_says_so(
    client, auth_headers, mock_ai_service
):
    tuesday, wednesday, thursday = (
        _on_weekday(1, 75),
        _on_weekday(2, 75),
        _on_weekday(3, 75),
    )
    user_id = await _seed(auth_headers, [tuesday, wednesday, thursday])

    # Since #295 a weekday limit holds for every such day only when said so;
    # Lena's message in the persona run says "immer" now.
    body = await _ask(client, auth_headers, LENA_ALWAYS)

    assert [c["label"] for c in body["capturedConstraints"]] == [
        "At most 60 min on Tuesdays",
        "At most 60 min on Thursdays",
    ]
    assert "Dienstags und donnerstags hast du höchstens 60 min" in notes_text(body)
    # The dates in the order they come, which depends on the day the test runs.
    dates = sorted([(tuesday["date"], "Dienstag"), (thursday["date"], "Donnerstag")])
    # As the athlete reads a date, "Dienstag 3.11." (ai-trainer-ops#338).
    shortened = ", ".join(
        f"{name} {int(day[8:10])}.{int(day[5:7])}." for day, name in dates
    )
    assert f"{shortened} gekürzt" in notes_text(body)
    stored = await _stored(user_id)
    assert [
        stored[d["date"]]["durationMinutes"] for d in (tuesday, wednesday, thursday)
    ] == [60, 75, 60]


@pytest.mark.parametrize(
    ("german", "note"),
    [
        (
            True,
            "Dienstags und donnerstags hast du höchstens 60 min, deshalb habe ich "
            "Donnerstag 29.10., Dienstag 3.11. gekürzt.",
        ),
        (
            False,
            "On Tuesdays and Thursdays you have at most 60 min, so I shortened "
            "Thursday Oct 29, Tuesday Nov 3.",
        ),
    ],
)
def test_the_note_names_the_weekdays_in_week_order(german, note):
    # From a Tuesday the next Thursday comes before the next Tuesday; the
    # weekdays are still said as the athlete says them, the dates as they come.
    constraints = [_cap(60, "tuesday"), _cap(60, "thursday")]

    assert (
        coach_chat._format_session_cap_note(
            ["2026-10-29", "2026-11-03"], constraints, german=german
        )
        == note
    )
