"""Backtest of the performance model against its later estimate (ai-trainer-ops#27).

The arithmetic is checked on hand-built attribute dicts. The database half
checks the one thing that makes it a backtest at all: the earlier estimate is
made without the rides that came after the cut-off.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
from auth import hash_password
from scripts import backtest_report
from scripts.backtest_report import Point
from services.analysis import compute_ride_performance_signals
from tests.conftest import TestSessionLocal
from tests.test_athlete_model_inference import _stream

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def test_compare_keeps_attributes_with_a_number_at_both_times():
    earlier = {
        "ftp": {"estimate": 250, "estimate_low": 240, "estimate_high": 270},
        "map": {"estimate": None, "score": "unknown"},
        "critical_speed": {"estimate": 4.0},
    }
    later = {
        "ftp": {"estimate": 260},
        "map": {"estimate": 330},
        "critical_speed": {"estimate": 4.2},
        "vo2max": {"estimate": 55},
    }

    assert backtest_report.compare(earlier, later) == [
        Point("critical_speed", 4.0, 4.2, None, None),
        Point("ftp", 250.0, 260.0, 240, 270),
    ]


def test_the_report_states_change_and_range_coverage():
    points = [
        Point("ftp", 240, 300, 230, 280),  # -20%, outside its range
        Point("ftp", 300, 300, 290, 310),  # 0%, inside
        Point("ftp", 330, 300, None, None),  # +10%, no range
        *[Point("critical_speed", 4.0, 4.0, None, None)] * 3,
        Point("map", 350, 340, None, None),
    ]

    text = backtest_report.render_markdown(points, 6, 3)

    assert "3 athletes with ride metrics." in text
    assert "| ftp | 3 | 10.0% | -3.3% | 1/2 |" in text
    assert "| critical_speed | 3 | 0.0% | +0.0% | no range stated |" in text
    # One athlete's row would be that athlete's own number.
    assert "| map |" not in text
    assert "Not shown, fewer than 3 athletes: map." in text


def test_an_empty_backtest_says_so():
    text = backtest_report.render_markdown([], 6, 0)
    assert "_No attribute had an estimate at both times._" in text


async def test_the_earlier_estimate_does_not_see_later_rides(monkeypatch):
    async with TestSessionLocal() as db:
        # Three athletes with a 30-minute effort at 250 W in July and one at
        # 300 W last week, and a fourth who only started last week, so has
        # nothing before the cut-off and must be left out.
        rides = ((1, "2026-07-20", 250), (2, "2026-10-03", 300))
        for n in range(4):
            user = await crud.create_user(
                db,
                email=f"backtest-{n}@example.com",
                name="B",
                hashed_password=hash_password("pw"),
            )
            for activity_id, day, watts in rides[1:] if n == 3 else rides:
                await crud.upsert_ride_metric(
                    db,
                    user.id,
                    strava_activity_id=activity_id,
                    activity_date=day,
                    perf_signals=compute_ride_performance_signals(
                        _stream(1800, watts, watts, 150, 152)
                    ),
                )
        await db.commit()
    monkeypatch.setattr(backtest_report, "async_session_maker", TestSessionLocal)

    text = await backtest_report.build(6, now=NOW)

    ftp_row = next(line for line in text.splitlines() if line.startswith("| ftp |"))
    assert "4 athletes with ride metrics." in text
    assert ftp_row.startswith("| ftp | 3 |")
    signed = float(ftp_row.split("|")[4].strip().rstrip("%"))
    # 250 W against 300 W is about a sixth lower (the estimate rounds); had the
    # earlier estimate seen last week's ride, the change would be 0.
    assert -18 < signed < -15


def test_the_script_parses_its_flag(monkeypatch, capsys):
    async def fake_build(weeks: int) -> str:
        return f"built weeks={weeks}"

    monkeypatch.setattr(backtest_report, "build", fake_build)
    assert backtest_report.main(["--weeks", "4"]) == 0
    assert "built weeks=4" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        backtest_report.main(["--weeks", "0"])
