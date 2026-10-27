"""The activity analysis and an upload mark matched plan days completed and
learn, as the sync does (ai-trainer-ops#301).

The owner's answer: both doors mark the plan days their rides matched, which
protects those days from an automated regenerate, and run the continuous-
learning step (#388). The step runs once per upload or analysis, never once per
ride: a .zip of a hundred rides costs one step.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

import crud
from auth import decode_token
from services import learning_pipeline
from services.dates import app_today
from tests.conftest import TestSessionLocal


def _user_id(auth_headers) -> str:
    return decode_token(auth_headers["Authorization"].split(" ", 1)[1])


@pytest.fixture
def learned(monkeypatch) -> list:
    """The athlete each learning step ran for, one entry per step."""
    runs: list = []

    async def spy(db, user, **_kwargs):
        runs.append(user.id if user is not None else None)
        return None

    monkeypatch.setattr(learning_pipeline, "learn_from_completed_workouts", spy)
    return runs


@pytest.fixture
def fake_fit(monkeypatch):
    import fitparse

    from tests.test_fit_history_upload_157 import _FitFile

    # The bytes of each file are the ride's age in days.
    monkeypatch.setattr(fitparse, "FitFile", _FitFile)


def _fit(age: int):
    return ("files", (f"{age}.fit", str(age).encode(), "application/octet-stream"))


def _days_ago(n: int) -> str:
    return (app_today() - timedelta(days=n)).isoformat()


async def _plan_rides_on(user_id: str, *days: str) -> None:
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(
            db,
            user_id,
            [
                {
                    "date": day,
                    "workoutType": "endurance",
                    "title": "Endurance Ride",
                    "description": "Steady Z2",
                    "durationMinutes": 60,
                }
                for day in days
            ],
        )
        await db.commit()


async def _completed(user_id: str) -> dict[str, bool]:
    async with TestSessionLocal() as db:
        row = await crud.get_training_plan(db, user_id)
    return {day["date"]: bool(day.get("completed")) for day in row.plan}


# --- a .fit upload --------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_upload_marks_the_plan_day_its_ride_matched(
    client, auth_headers, mock_ai_service, fake_fit, learned
):
    user_id = _user_id(auth_headers)
    await _plan_rides_on(user_id, _days_ago(1), _days_ago(0))

    response = await client.post(
        "/api/v1/users/me/upload-fit",
        headers=auth_headers,
        files={"file": ("1.fit", b"1", "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    assert await _completed(user_id) == {_days_ago(1): True, _days_ago(0): False}
    assert learned == [user_id]


@pytest.mark.asyncio
async def test_a_bulk_upload_of_several_files_learns_once(
    client, auth_headers, mock_ai_service, fake_fit, learned
):
    user_id = _user_id(auth_headers)
    await _plan_rides_on(user_id, _days_ago(2), _days_ago(1))

    response = await client.post(
        "/api/v1/users/me/upload-fit/bulk",
        headers=auth_headers,
        files=[_fit(age) for age in (2, 1, 40)],
    )

    assert response.status_code == 200 and response.json()["imported"] == 3, (
        response.text
    )
    assert await _completed(user_id) == {_days_ago(2): True, _days_ago(1): True}
    assert learned == [user_id]


@pytest.mark.asyncio
async def test_a_part_the_app_sends_with_learn_false_does_not_learn(
    client, auth_headers, mock_ai_service, fake_fit, learned
):
    """A large selection goes up in parts; only the last one learns."""
    user_id = _user_id(auth_headers)
    await _plan_rides_on(user_id, _days_ago(1))

    response = await client.post(
        "/api/v1/users/me/upload-fit/bulk?learn=false",
        headers=auth_headers,
        files=[_fit(age) for age in (1, 40)],
    )

    assert response.status_code == 200 and response.json()["imported"] == 2, (
        response.text
    )
    # The day is marked all the same: that costs nothing.
    assert await _completed(user_id) == {_days_ago(1): True}
    assert learned == []


@pytest.mark.asyncio
async def test_an_upload_that_imports_nothing_does_not_learn(
    client, auth_headers, mock_ai_service, fake_fit, learned
):
    user_id = _user_id(auth_headers)
    first = await client.post(
        "/api/v1/users/me/upload-fit/bulk", headers=auth_headers, files=[_fit(40)]
    )
    again = await client.post(
        "/api/v1/users/me/upload-fit/bulk", headers=auth_headers, files=[_fit(40)]
    )

    assert first.json()["imported"] == 1, first.text
    assert again.json()["imported"] == 0, again.text
    assert learned == [user_id]


# --- the activity analysis -------------------------------------------------------


def _activity(activity_id: int, days_ago: int) -> dict:
    return {
        "id": activity_id,
        "externalId": f"i{activity_id}",
        "name": "Morning endurance",
        "type": "Ride",
        "distance": 40000,
        "movingTime": 3600,
        "elapsedTime": 3600,
        "totalElevationGain": 300,
        "startDate": f"{_days_ago(days_ago)}T08:00:00Z",
    }


@pytest.mark.asyncio
async def test_the_analysis_marks_the_matched_days_and_learns_once(
    client, auth_headers, mock_ai_service, learned
):
    user_id = _user_id(auth_headers)
    await _plan_rides_on(user_id, _days_ago(2), _days_ago(1), _days_ago(0))

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [_activity(71001, 2), _activity(71002, 1)],
            "source": "intervals",
        },
    )

    assert response.status_code == 200, response.text
    assert await _completed(user_id) == {
        _days_ago(2): True,
        _days_ago(1): True,
        _days_ago(0): False,
    }
    assert learned == [user_id]


@pytest.mark.asyncio
async def test_an_analysis_that_stores_no_ride_does_not_learn(
    client, auth_headers, mock_ai_service, learned
):
    """The second look at an intervals.icu ride finds it stored and skips it."""
    user_id = _user_id(auth_headers)
    body = {"activities": [_activity(71003, 3)], "source": "intervals"}

    first = await client.post(
        "/api/v1/ai/analyse-activities", headers=auth_headers, json=body
    )
    again = await client.post(
        "/api/v1/ai/analyse-activities", headers=auth_headers, json=body
    )

    assert first.status_code == 200 and again.status_code == 200, again.text
    assert learned == [user_id]


@pytest.mark.asyncio
async def test_an_analysis_whose_rides_could_not_be_priced_does_not_learn(
    client, auth_headers, mock_ai_service, learned, monkeypatch
):
    from services import ride_ingest

    monkeypatch.setattr(ride_ingest, "build_ride_metrics_chain", lambda *a, **k: [])

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [_activity(71004, 1)], "source": "intervals"},
    )

    assert response.status_code == 200, response.text
    assert learned == []


# --- the step itself, in a session of its own ------------------------------------


@pytest.mark.asyncio
async def test_the_step_commits_what_it_learned_in_its_own_session(
    client, auth_headers, monkeypatch
):
    user_id = _user_id(auth_headers)

    async def learn(db, user, **_kwargs):
        await crud.create_chat_message(
            db,
            user.id,
            role="assistant",
            content="learned from the upload",
            timestamp="2026-10-27T08:00:00+00:00",
        )

    monkeypatch.setattr(learning_pipeline, "learn_from_completed_workouts", learn)

    await learning_pipeline.learn_in_own_session(TestSessionLocal, user_id)

    async with TestSessionLocal() as db:
        messages = await crud.get_chat_messages(db, user_id)
    assert [m.content for m in messages] == ["learned from the upload"]


@pytest.mark.asyncio
async def test_a_failed_step_is_logged_and_not_raised(
    client, auth_headers, monkeypatch, caplog
):
    async def fail(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(learning_pipeline, "learn_from_completed_workouts", fail)

    with caplog.at_level(logging.WARNING, logger="services.learning_pipeline"):
        await learning_pipeline.learn_in_own_session(
            TestSessionLocal, _user_id(auth_headers)
        )

    assert "Continuous learning after a request failed" in caplog.text


@pytest.mark.asyncio
async def test_an_athlete_gone_by_then_is_not_learned_about(learned):
    await learning_pipeline.learn_in_own_session(TestSessionLocal, "no-such-user")

    assert learned == []
