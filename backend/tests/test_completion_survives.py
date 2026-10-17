"""A session the athlete did stays done (ai-trainer-ops#111).

Three gaps let a done session come back:

1. ``PUT /users/me/plan`` cleared ``completed`` on every day it received, and
   the web app saves the whole plan after every hand-logged session. A day a
   synced ride had completed was open again after the next log.
2. Nothing marked a hand-logged session completed. Only ride matching set the
   flag, so an athlete without a device had every logged session open.
3. The nightly job reads an open past session as missed and asks the model to
   reschedule it. So it copied the done session onto a later day.

These tests drive the routes the browser calls and the nightly job itself,
with the model replaced by a writer that does exactly that copy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import crud
import models
from services import logged_sessions, plan_maintenance, plan_pipeline, ride_matching
from services.dates import app_today
from services.ride_matching import MATCH_AUTO
from tests.conftest import TestSessionLocal

EMAIL = "rider@example.com"

FEEDBACK = {
    "actualDurationMinutes": 58,
    "perceivedEffort": 6,
    "notes": "Felt fine.",
    "completedAt": "2026-10-05T19:30:00Z",
}


def _iso(today, offset: int) -> str:
    return (today + timedelta(days=offset)).isoformat()


def _plan(today) -> list[dict]:
    """Yesterday's key session and a fortnight of easy riding after it."""
    plan = [
        {
            "date": _iso(today, offset),
            "workoutType": "endurance",
            "title": f"Easy {offset}",
            "description": "Easy aerobic ride",
            "durationMinutes": 60,
        }
        for offset in range(-1, 13)
    ]
    plan[0] = {
        "date": _iso(today, -1),
        "workoutType": "intervals",
        "title": "VO2 5x4",
        "description": "Five times four minutes hard",
        "durationMinutes": 75,
    }
    return plan


async def _user(db) -> models.User:
    return await db.scalar(select(models.User).where(models.User.email == EMAIL))


async def _seed(today, now) -> None:
    """The onboarded rider with a generated plan, as production has it."""
    async with TestSessionLocal() as db:
        user = await _user(db)
        user.is_onboarded = True
        await plan_pipeline.commit_plan(
            db, user, _plan(today), base_plan=[], source="generate", now=now
        )
        await db.commit()


async def _complete_by_sync(today, offset: int) -> None:
    """Activity sync matching a ride to that day, the way ride matching does."""
    async with TestSessionLocal() as db:
        user = await _user(db)
        current = (await crud.get_training_plan(db, user.id)).plan
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": _iso(today, offset), "completed": True}],
            base_plan=current,
            source="activity_import",
        )
        await db.commit()


async def _stored_plan() -> list[dict]:
    async with TestSessionLocal() as db:
        user = await _user(db)
        row = await crud.get_training_plan(db, user.id)
        return row.plan if row is not None else []


def _day(plan: list[dict], date: str, slot: int = 0) -> dict:
    return next(d for d in plan if d["date"] == date and int(d.get("slot") or 0) == slot)


async def _add_ride(today, offset: int, *, matched: bool) -> None:
    """A recorded ride on that day, matched to its session or not."""
    async with TestSessionLocal() as db:
        user = await _user(db)
        metric = await crud.upsert_ride_metric(
            db,
            user.id,
            strava_activity_id=9_000 + offset,
            activity_source="strava",
            external_activity_id=str(9_000 + offset),
            activity_date=_iso(today, offset),
            sport_type="cycling",
            duration_seconds=4_500,
        )
        if matched:
            metric.matched_plan_date = _iso(today, offset)
            metric.matched_plan_slot = 0
            metric.plan_match_status = MATCH_AUTO
        await db.commit()


@pytest.fixture
def nightly_writer(monkeypatch):
    """The nightly job with a model that reschedules yesterday's VO2 to tomorrow.

    That is the textbook answer to the adapt prompt's "reschedule these" note,
    so if it runs, a done session is copied. ``calls`` counts the runs. Week
    targets are held still so the only reasons left are the plan's own.
    """
    calls: list[int] = []

    async def copy_yesterdays_key_session(plan, *args, **kwargs):
        calls.append(1)
        today = app_today()
        return [
            {
                "date": _iso(today, 1),
                "workoutType": "intervals",
                "title": "VO2 5x4",
                "description": "Five times four minutes hard",
                "durationMinutes": 75,
            }
        ]

    async def nothing(*args, **kwargs):
        return ""

    async def no_narration(*args, **kwargs):
        return None

    async def targets_unchanged(*args, **kwargs):
        return False

    monkeypatch.setattr(
        plan_maintenance.ai_service, "adapt_training_plan", copy_yesterdays_key_session
    )
    monkeypatch.setattr(plan_maintenance, "training_weather_context_for_user", nothing)
    monkeypatch.setattr(
        plan_maintenance.coach_summary, "narrate_plan_changes", no_narration
    )
    monkeypatch.setattr(plan_maintenance, "_week_targets_moved", targets_unchanged)
    return calls


# ---------------------------------------------------------------------------
# 1. A plan save keeps what the server recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_plan_save_keeps_a_day_a_synced_ride_completed(client, auth_headers):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)
    await _complete_by_sync(today, -1)

    # What the web app sends after a hand-logged session: the plan it holds.
    held = (await client.get("/api/v1/users/me/plan", headers=auth_headers)).json()["plan"]
    without_flags = [{k: v for k, v in d.items() if k != "completed"} for d in held]
    saved = await client.put(
        "/api/v1/users/me/plan", headers=auth_headers, json={"plan": without_flags}
    )
    assert saved.status_code == 200, saved.text

    yesterday = _day(await _stored_plan(), _iso(today, -1))
    assert yesterday.get("completed") is True
    # Unchanged, so not re-stamped as the athlete's own edit (which pins it).
    assert yesterday.get("source") == "activity_import"


@pytest.mark.asyncio
async def test_a_plan_save_still_cannot_complete_an_open_day(client, auth_headers):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)

    held = (await client.get("/api/v1/users/me/plan", headers=auth_headers)).json()["plan"]
    forged = [
        {**d, "completed": True} if d["date"] == _iso(today, 2) else d for d in held
    ]
    saved = await client.put(
        "/api/v1/users/me/plan", headers=auth_headers, json={"plan": forged}
    )
    assert saved.status_code == 200, saved.text

    assert not _day(await _stored_plan(), _iso(today, 2)).get("completed")


@pytest.mark.asyncio
async def test_a_plan_save_keeps_stored_feedback_and_drops_an_unreadable_one(
    client, auth_headers
):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)
    stored = await _stored_plan()
    stored = [
        {**d, "feedback": {**FEEDBACK}} if d["date"] == _iso(today, -1)
        else {**d, "feedback": {"notes": "no duration, no effort"}}
        if d["date"] == _iso(today, 0)
        else d
        for d in stored
    ]
    async with TestSessionLocal() as db:
        user = await _user(db)
        await crud.upsert_training_plan(db, user.id, stored)
        await db.commit()

    sent = [{k: v for k, v in d.items() if k != "feedback"} for d in stored]
    saved = await client.put(
        "/api/v1/users/me/plan", headers=auth_headers, json={"plan": sent}
    )
    assert saved.status_code == 200, saved.text

    after = await _stored_plan()
    assert _day(after, _iso(today, -1))["feedback"]["actualDurationMinutes"] == 58
    assert "feedback" not in _day(after, _iso(today, 0))


@pytest.mark.asyncio
async def test_marking_matched_rides_reports_a_plan_with_nothing_to_mark():
    assert await ride_matching.mark_matched_days_completed(None, None, [], []) is False


# ---------------------------------------------------------------------------
# 2. A hand-logged session is done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logging_a_planned_session_marks_it_completed(client, auth_headers):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)

    logged = await client.post(
        f"/api/v1/users/me/workouts/{_iso(today, -1)}",
        headers=auth_headers,
        json={"feedback": FEEDBACK},
    )
    assert logged.status_code == 200, logged.text

    yesterday = _day(await _stored_plan(), _iso(today, -1))
    assert yesterday.get("completed") is True
    history = await client.get(
        f"/api/v1/users/me/plan-history?date={_iso(today, -1)}", headers=auth_headers
    )
    assert history.status_code == 200, history.text
    assert "workout_log" in {row["source"] for row in history.json()["entries"]}


@pytest.mark.asyncio
async def test_a_log_without_a_duration_leaves_the_session_open(client, auth_headers):
    """The load chain's rule: a log claims a session when it has a duration."""
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)

    logged = await client.post(
        f"/api/v1/users/me/workouts/{_iso(today, -1)}",
        headers=auth_headers,
        json={"feedback": {**FEEDBACK, "actualDurationMinutes": 0}},
    )
    assert logged.status_code == 200, logged.text

    assert not _day(await _stored_plan(), _iso(today, -1)).get("completed")


@pytest.mark.asyncio
async def test_logging_the_evening_session_leaves_the_morning_open(client, auth_headers):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    plan = _plan(today)
    plan.append({**plan[1], "slot": 1, "title": "Evening spin"})
    async with TestSessionLocal() as db:
        user = await _user(db)
        user.is_onboarded = True
        await plan_pipeline.commit_plan(
            db, user, plan, base_plan=[], source="generate", now=now
        )
        await db.commit()

    logged = await client.post(
        f"/api/v1/users/me/workouts/{_iso(today, 0)}",
        headers=auth_headers,
        json={"feedback": FEEDBACK, "slot": 1},
    )
    assert logged.status_code == 200, logged.text

    stored = await _stored_plan()
    assert _day(stored, _iso(today, 0), slot=1).get("completed") is True
    assert not _day(stored, _iso(today, 0), slot=0).get("completed")


@pytest.mark.asyncio
async def test_a_log_for_a_day_the_plan_does_not_have_changes_no_plan(client, auth_headers):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)
    before = await _stored_plan()

    logged = await client.post(
        f"/api/v1/users/me/workouts/{_iso(today, -30)}",
        headers=auth_headers,
        json={"feedback": FEEDBACK},
    )
    assert logged.status_code == 200, logged.text

    assert await _stored_plan() == before


@pytest.mark.asyncio
async def test_a_failed_plan_write_never_loses_the_log(client, auth_headers, monkeypatch):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)

    async def broken(*args, **kwargs):
        raise RuntimeError("plan write failed")

    monkeypatch.setattr(logged_sessions, "complete_logged_sessions", broken)
    logged = await client.post(
        f"/api/v1/users/me/workouts/{_iso(today, -1)}",
        headers=auth_headers,
        json={"feedback": FEEDBACK},
    )
    assert logged.status_code == 200, logged.text

    async with TestSessionLocal() as db:
        user = await _user(db)
        logs = await crud.get_workout_logs(db, user.id)
    assert [(log.date, log.actual_duration_minutes) for log in logs] == [
        (_iso(today, -1), 58)
    ]


# ---------------------------------------------------------------------------
# 3. The nightly job does not repeat a done session
# ---------------------------------------------------------------------------


async def _run_nightly(now) -> None:
    await plan_maintenance.run_daily_plan_maintenance(TestSessionLocal, now=now)


@pytest.mark.asyncio
async def test_a_ridden_session_is_not_repeated_after_a_log(
    client, auth_headers, nightly_writer
):
    """The issue's sequence: sync, then a hand log and its plan save, then 02:00."""
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)
    await _complete_by_sync(today, -1)
    logged = await client.post(
        f"/api/v1/users/me/workouts/{_iso(today, 0)}",
        headers=auth_headers,
        json={"feedback": FEEDBACK},
    )
    assert logged.status_code == 200, logged.text
    held = (await client.get("/api/v1/users/me/plan", headers=auth_headers)).json()["plan"]
    saved = await client.put(
        "/api/v1/users/me/plan", headers=auth_headers, json={"plan": held}
    )
    assert saved.status_code == 200, saved.text

    await _run_nightly(now)

    assert nightly_writer == [], "the nightly job read a done session as missed"
    stored = await _stored_plan()
    assert _day(stored, _iso(today, -1)).get("completed") is True
    assert _day(stored, _iso(today, 1))["workoutType"] == "endurance"


@pytest.mark.asyncio
async def test_a_hand_logged_session_is_not_repeated(client, auth_headers, nightly_writer):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)
    logged = await client.post(
        f"/api/v1/users/me/workouts/{_iso(today, -1)}",
        headers=auth_headers,
        json={"feedback": FEEDBACK},
    )
    assert logged.status_code == 200, logged.text

    await _run_nightly(now)

    assert nightly_writer == []
    assert _day(await _stored_plan(), _iso(today, -1))["workoutType"] == "intervals"


@pytest.mark.asyncio
async def test_the_nightly_job_heals_a_log_the_plan_never_heard_of(
    client, auth_headers, nightly_writer
):
    """A log saved before #111 left its session open; the next night closes it."""
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)
    # Written past the route, the way every log before #111 reached the plan.
    async with TestSessionLocal() as db:
        user = await _user(db)
        await crud.upsert_workout_log(
            db,
            user.id,
            _iso(today, -1),
            slot=0,
            actual_duration_minutes=70,
            perceived_effort=7,
            average_power=None,
            average_heart_rate=None,
            peak_power=None,
            notes="",
            completed_at=f"{_iso(today, -1)}T18:00:00",
            sport_type="cycling",
        )
        await db.commit()
    assert not _day(await _stored_plan(), _iso(today, -1)).get("completed")

    await _run_nightly(now)

    assert nightly_writer == []
    yesterday = _day(await _stored_plan(), _iso(today, -1))
    assert yesterday.get("completed") is True
    assert yesterday.get("source") == "workout_log"


@pytest.mark.asyncio
async def test_the_nightly_job_heals_a_matched_ride_a_plan_save_cleared(
    client, auth_headers, nightly_writer
):
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)
    # Matched by sync, then its flag cleared by a plan save before #111.
    await _add_ride(today, -1, matched=True)

    await _run_nightly(now)

    assert nightly_writer == []
    assert _day(await _stored_plan(), _iso(today, -1)).get("completed") is True


@pytest.mark.asyncio
async def test_a_day_the_athlete_trained_is_not_missed_even_unmatched(
    client, auth_headers, nightly_writer
):
    """A ride that matched nothing still says the athlete trained that day."""
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)
    await _add_ride(today, -1, matched=False)

    await _run_nightly(now)

    assert nightly_writer == []
    # Not marked done either: an unmatched ride proves training, not the session.
    assert not _day(await _stored_plan(), _iso(today, -1)).get("completed")


@pytest.mark.asyncio
async def test_a_truly_missed_session_is_still_rescheduled(
    client, auth_headers, nightly_writer
):
    """The control: nothing recorded, nothing logged, so the run is right."""
    now = datetime.now(timezone.utc)
    today = app_today(now)
    await _seed(today, now)

    await _run_nightly(now)

    assert nightly_writer == [1]
