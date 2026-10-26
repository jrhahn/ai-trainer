"""The ride history says which rides were today and yesterday (ai-trainer-ops#148)."""

from datetime import date
from types import SimpleNamespace

from services.prompts import ride_metrics_context_section, todays_rides_line

TODAY = date(2026, 10, 3)  # a Saturday


def _ride(day: str, minutes: int | None, sport: str | None = "cycling"):
    return SimpleNamespace(
        activity_date=day,
        sport_type=sport,
        duration_seconds=minutes * 60 if minutes else None,
    )


def test_the_line_names_todays_and_yesterdays_rides():
    rides = [
        _ride("2026-10-03", 233),
        _ride("2026-10-02", 58),
        _ride("2026-10-02", None, "run"),
        _ride("2026-09-30", 90),
    ]

    line = todays_rides_line(rides, TODAY)

    assert line.startswith(
        "Ridden today (Saturday, 2026-10-03): cycling 233 min. "
        "Ridden yesterday (Friday, 2026-10-02): cycling 58 min, run. "
    )
    assert "never call it yesterday's" in line


def test_a_day_without_a_ride_says_nothing_was_ridden():
    line = todays_rides_line([_ride("2026-10-02", 58, None)], TODAY)

    assert "Ridden today (Saturday, 2026-10-03): nothing." in line
    assert "Ridden yesterday (Friday, 2026-10-02): activity 58 min." in line


def test_the_history_section_carries_the_line_after_the_rides():
    section = ride_metrics_context_section(
        [_ride("2026-10-03", 233)], "UTC", today=TODAY
    )

    lines = section.splitlines()
    assert lines[1].startswith("  2026-10-03 (Saturday, today)")
    assert any(
        line.startswith("Ridden today (Saturday, 2026-10-03): cycling 233 min.")
        for line in lines[2:]
    )
