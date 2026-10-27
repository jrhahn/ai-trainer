"""A ride reaches the coach with its distance beside its minutes, and a reply
that gives minutes as km is a finding (ai-trainer-ops#318).

The coach called a 250-minute ride of about 61 km "250 km". The ride's line
gave 250 as its only size, and the number check, which reads no units, found
the 250 in the context.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from services import output_audit
from services.prompts import ride_metrics_context_section, todays_rides_line

TODAY = date(2026, 10, 27)


def _ride(day: str = "2026-10-25", minutes: int = 250, km: float | None = 61.2):
    return SimpleNamespace(
        activity_date=day,
        sport_type="cycling",
        duration_seconds=minutes * 60,
        distance_km=km,
    )


def test_the_ride_line_gives_the_distance_beside_the_minutes():
    section = ride_metrics_context_section([_ride()], today=TODAY)

    assert "duration 250 min" in section
    assert "distance 61.2 km" in section


def test_a_session_without_a_distance_names_none():
    section = ride_metrics_context_section([_ride(km=None)], today=TODAY)

    assert "duration 250 min" in section
    assert "km" not in section


def test_the_line_on_todays_rides_gives_the_distance_too():
    line = todays_rides_line(
        [_ride("2026-10-27"), _ride("2026-10-26", 45, None)], TODAY
    )

    assert "cycling 250 min (61.2 km)." in line
    assert "cycling 45 min." in line


RIDE_LINE = "2026-10-25 (Sunday, 2 days ago) | sport:cycling | duration 250 min"


@pytest.mark.parametrize(
    "context",
    [
        [RIDE_LINE],
        ["Ridden yesterday (Sunday, 2026-10-25): cycling 250 min."],
        ['{"date": "2026-10-25", "durationMinutes": 250}'],
        [{"date": "2026-10-25", "durationMinutes": 250}],
    ],
)
def test_minutes_given_as_km_are_a_finding(context):
    findings = output_audit.unexplained_numbers(
        "Deine 250 km am Sonntag waren eine starke Grundlage.", context
    )

    assert [f.detail for f in findings] == ["250"]
    assert "minutes" in findings[0].reason


@pytest.mark.parametrize(
    "reply, context",
    [
        # The distance the context gives.
        ("Deine 61 km am Sonntag.", [RIDE_LINE + " | distance 61.2 km"]),
        # The same number as a distance elsewhere: the athlete's race.
        ("Die 250 km im Mai.", [RIDE_LINE, "Radmarathon 250 km im Mai"]),
        # Minutes given as minutes.
        ("Deine 250 min am Sonntag.", [RIDE_LINE]),
        # A small number stays enumeration, as everywhere in the check.
        ("Lauf 10 km locker.", ["Warm-up 10 min"]),
    ],
)
def test_a_distance_the_context_has_as_one_is_none(reply, context):
    assert output_audit.unexplained_numbers(reply, context) == []
