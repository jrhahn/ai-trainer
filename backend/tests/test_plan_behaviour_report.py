"""How the coach's plans behave over weeks (ai-trainer-ops#66)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import crud
import models
from scripts import plan_behaviour_report as pbr
from tests.conftest import TestSessionLocal

TODAY = date(2026, 10, 11)


def _week(start: date, sessions: list[tuple[str, int]]):
    """One history row per day of the week starting at ``start``."""
    return [
        SimpleNamespace(
            date=(start + timedelta(days=i)).isoformat(),
            slot=0,
            applied=True,
            recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            new_day={"workoutType": kind, "durationMinutes": minutes},
        )
        for i, (kind, minutes) in enumerate(sessions)
    ]


SAME_WEEK = [
    ("endurance", 90),
    ("intervals", 60),
    ("rest", 0),
    ("tempo", 60),
    ("recovery", 45),
    ("endurance", 180),
    ("endurance", 120),
]


def _history(weekly: list[list[tuple[str, int]]]):
    starts = pbr._week_starts(TODAY, len(weekly))
    rows = []
    for start, sessions in zip(starts, weekly):
        rows += _week(start, sessions)
    return rows


def test_the_same_fortnight_forever_has_no_recovery_week():
    result = pbr.behaviour(_history([SAME_WEEK] * 12), [], TODAY, 12)

    assert result.planned_recovery_weeks == 0
    assert result.planned_cv == pytest.approx(0)
    assert result.mean_similarity == pytest.approx(1)
    assert result.hard_share_spread == pytest.approx(0)


def test_a_periodized_plan_shows_its_rhythm():
    light = [(k, m // 2) for k, m in SAME_WEEK]
    vo2 = [("intervals" if k == "tempo" else k, m) for k, m in SAME_WEEK]
    weeks = [SAME_WEEK, SAME_WEEK, SAME_WEEK, light] * 2 + [vo2, vo2, vo2, light]
    result = pbr.behaviour(_history(weeks), [], TODAY, 12)

    assert result.planned_recovery_weeks == 3
    assert result.planned_cv > 0.1
    assert result.hard_share_spread > 0


def test_the_last_applied_version_of_a_day_wins_and_blocked_rows_do_not():
    day = "2026-10-01"
    rows = [
        SimpleNamespace(date=day, slot=0, applied=True,
                        recorded_at=datetime(2026, 9, 1), new_day={"workoutType": "rest"}),
        SimpleNamespace(date=day, slot=0, applied=True,
                        recorded_at=datetime(2026, 9, 2), new_day={"workoutType": "tempo"}),
        SimpleNamespace(date=day, slot=0, applied=False,
                        recorded_at=datetime(2026, 9, 3), new_day={"workoutType": "race"}),
    ]

    plan = pbr.final_plan(rows)

    assert plan[(date(2026, 10, 1), 0)]["workoutType"] == "tempo"


def test_too_little_history_is_not_a_finding():
    assert pbr.behaviour(_history([SAME_WEEK] * 2), [], TODAY, 12) is None


def test_ridden_volume_is_measured_alongside():
    rides = [
        SimpleNamespace(activity_date=(TODAY - timedelta(days=d)).isoformat(),
                        duration_seconds=3600)
        for d in range(1, 8)
    ]
    result = pbr.behaviour(_history([SAME_WEEK] * 6), rides, TODAY, 6)

    assert result.ridden_minutes[-1] == 420


def test_the_aggregate_hides_fewer_than_three_athletes():
    result = pbr.behaviour(_history([SAME_WEEK] * 6), [], TODAY, 6)

    assert "Not shown" in pbr.render_aggregate([result], 1)
    table = pbr.render_aggregate([result] * 3, 3)
    assert "no planned recovery week at all: 3 of 3" in table


@pytest.mark.asyncio
async def test_one_athlete_reads_their_own_figures(monkeypatch):
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email="behaviour@example.com", name="R", hashed_password="x"
        )
        for row in _history([SAME_WEEK] * 6):
            db.add(
                models.PlanDayHistory(
                    user_id=user.id,
                    date=row.date,
                    slot=0,
                    new_day=row.new_day,
                    source="generate",
                    applied=True,
                    recorded_at=row.recorded_at,
                )
            )
        await db.commit()
    monkeypatch.setattr(pbr, "async_session_maker", TestSessionLocal)

    own = await pbr.build(6, "behaviour@example.com", today=TODAY)
    aggregate = await pbr.build(6, today=TODAY)

    assert "Recovery weeks" in own and "planned 0" in own
    assert "Not shown" in aggregate
