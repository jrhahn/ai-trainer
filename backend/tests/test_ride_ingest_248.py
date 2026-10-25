"""Every door stores its rides through one ingest, in one order (ai-trainer-ops#248).

The table below pins which steps each door's policy runs and in what order. A
door that should take a step another one takes changes its policy here, where
the difference is visible, instead of growing a copy of the chain.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import crud
from auth import decode_token
from services import (
    activity_sync,
    logged_sessions,
    metrics_service,
    reported_effort,
    ride_ingest,
    rides_pipeline,
)
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user

EXPECTED_STEPS = {
    "SYNC": [
        "annotate", "price", "fallback", "store", "replay", "reconcile",
        "last_ride_feedback", "match", "notify", "complete_days", "review", "learn",
    ],
    "ANALYSIS": [
        "annotate", "price", "fallback", "store", "replay", "reconcile", "match", "notify", "review",
    ],
    "FIT_UPLOAD": ["annotate", "price", "fallback", "store", "reconcile", "match", "notify"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(EXPECTED_STEPS))
async def test_each_door_runs_its_steps_in_the_fixed_order(door, monkeypatch):
    """One ride, dated before the newest stored one and becoming the newest by id,
    matched to the plan: every optional step has what it needs to run."""
    steps: list[str] = []

    def step(name, returns=None):
        async def record(*_args, **_kwargs):
            steps.append(name)
            return returns

        return record

    def plain_step(name, returns=None):
        def record(*_args, **_kwargs):
            steps.append(name)
            return returns

        return record

    latest = iter([SimpleNamespace(id="before", activity_date="2026-06-10"), SimpleNamespace(id="after")])

    async def get_latest(*_args, **_kwargs):
        return next(latest)

    async def no_plan(*_args, **_kwargs):
        return None

    metric = {"strava_activity_id": 1, "activity_date": "2026-06-01"}
    monkeypatch.setattr(reported_effort, "annotate_from_logs", step("annotate"))
    monkeypatch.setattr(ride_ingest, "build_ride_metrics_chain", plain_step("price", [metric]))
    monkeypatch.setattr(ride_ingest, "apply_summary_fallback", plain_step("fallback"))
    monkeypatch.setattr(crud, "upsert_ride_metric", step("store"))
    monkeypatch.setattr(ride_ingest, "replay_load_chain", step("replay"))
    monkeypatch.setattr(logged_sessions, "reconcile_logged_sessions", step("reconcile"))
    monkeypatch.setattr(metrics_service, "refresh_rider_assessment_feedback", step("last_ride_feedback"))
    monkeypatch.setattr(metrics_service, "get_effective_threshold_pace", step("threshold_pace"))
    monkeypatch.setattr(
        ride_ingest, "apply_ride_plan_matches", step("match", [SimpleNamespace(strava_activity_id=1)])
    )
    monkeypatch.setattr(rides_pipeline, "notify_changed", step("notify"))
    monkeypatch.setattr(ride_ingest, "mark_matched_days_completed", step("complete_days"))
    monkeypatch.setattr(ride_ingest, "review_matched_ride_and_adapt", step("review"))
    monkeypatch.setattr(ride_ingest, "learn_from_completed_workouts", step("learn"))
    monkeypatch.setattr(ride_ingest, "resolve_user_provider", lambda _user: "gemini")
    monkeypatch.setattr(crud, "get_latest_ride_metric", get_latest)
    monkeypatch.setattr(crud, "get_training_plan", no_plan)
    user = SimpleNamespace(id="u-248", max_heart_rate=None, resting_heart_rate=None)

    async with TestSessionLocal() as db:
        result = await ride_ingest.ingest_rides(
            db, user, [{"strava_activity_id": 1, "activity_date": "2026-06-01"}],
            ftp=250.0, policy=getattr(ride_ingest, door),
        )

    assert [s for s in steps if s != "threshold_pace"] == EXPECTED_STEPS[door]
    assert result.stored == 1


# --- each door hands its rides over with its own policy -----------------------


def _spy_on_ingest(monkeypatch) -> list[ride_ingest.IngestPolicy]:
    used: list[ride_ingest.IngestPolicy] = []
    real = ride_ingest.ingest_rides

    async def spy(db, user, rides, *, ftp, policy):
        used.append(policy)
        return await real(db, user, rides, ftp=ftp, policy=policy)

    monkeypatch.setattr(ride_ingest, "ingest_rides", spy)
    return used


@pytest.mark.asyncio
async def test_the_sync_uses_the_sync_policy(monkeypatch):
    user_id = await _create_user(email="sync-248@example.com", strava=True, strava_cursor=100)

    async def fetch(*_args, **_kwargs):
        return [{"id": 101, "name": "Ride", "type": "Ride", "sport_type": "Ride",
                 "start_date": "2026-06-10T06:00:00Z", "start_date_local": "2026-06-10T08:00:00",
                 "elapsed_time": 3600}]

    async def streams(*_args, **_kwargs):
        return {}

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(activity_sync, "fetch_recent_strava_activities", fetch)
    monkeypatch.setattr(activity_sync, "fetch_strava_activity_streams", streams)
    monkeypatch.setattr(activity_sync, "enrich_activity_weather", streams)
    monkeypatch.setattr(ride_ingest, "learn_from_completed_workouts", nothing)
    used = _spy_on_ingest(monkeypatch)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await activity_sync.sync_strava_for_user(db, user)
        await db.commit()

    assert used == [ride_ingest.SYNC]


@pytest.mark.asyncio
async def test_the_analysis_uses_the_analysis_policy(client, auth_headers, mock_ai_service, monkeypatch):
    used = _spy_on_ingest(monkeypatch)

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [{"id": 1, "name": "Ride", "type": "Ride", "distance": 50000,
                              "movingTime": 3600, "elapsedTime": 3700, "totalElevationGain": 500,
                              "startDate": "2026-04-10T08:00:00Z", "averageWatts": 220}]},
    )

    assert response.status_code == 200, response.text
    assert used == [ride_ingest.ANALYSIS]


@pytest.mark.asyncio
async def test_a_fit_upload_uses_the_upload_policy(client, auth_headers, mock_ai_service, monkeypatch):
    import fitparse

    from tests.test_fit_history_upload_157 import _FitFile

    monkeypatch.setattr(fitparse, "FitFile", _FitFile)
    used = _spy_on_ingest(monkeypatch)

    response = await client.post(
        "/api/v1/users/me/upload-fit/bulk",
        headers=auth_headers,
        files=[("files", (f"{age}.fit", str(age).encode(), "application/octet-stream")) for age in (30, 31)],
    )

    assert response.status_code == 200 and response.json()["imported"] == 2, response.text
    assert used == [ride_ingest.FIT_UPLOAD, ride_ingest.FIT_UPLOAD]


# --- what the analysis gains from the shared steps ------------------------------


def _ride(activity_id: int, day: str) -> dict:
    return {
        "strava_activity_id": activity_id,
        "activity_source": "strava",
        "external_activity_id": str(activity_id),
        "activity_date": day,
        "sport_type": "cycling",
        "duration_seconds": 3600,
        "streams": {},
        "_summary_tss": 80.0,
    }


@pytest.mark.asyncio
async def test_a_late_ride_from_the_analysis_is_counted_by_the_rides_after_it():
    """The analysis chained a ride dated before the newest stored one as if it came
    after it, and never replayed: the newer ride's fitness never counted it (#278)."""
    user_id = await _create_user(email="late-248@example.com")
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await ride_ingest.ingest_rides(db, user, [_ride(2, "2026-06-10")], ftp=250.0, policy=ride_ingest.FIT_UPLOAD)
        await db.commit()
        before = (await crud.get_latest_ride_metric(db, user_id)).ctl_after

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await ride_ingest.ingest_rides(db, user, [_ride(1, "2026-06-03")], ftp=250.0, policy=ride_ingest.ANALYSIS)
        await db.commit()
        after = (await crud.get_latest_ride_metric(db, user_id)).ctl_after

    assert after > before


@pytest.mark.asyncio
async def test_each_review_sees_the_plan_the_previous_review_left(monkeypatch):
    """The analysis handed every matched ride the plan as it stood before the first
    review, so a later adaptation looked like a concurrent edit (#452)."""
    user_id = await _create_user(email="review-248@example.com")
    seen: list[list[dict]] = []

    async def matched(*_args, **_kwargs):
        return [SimpleNamespace(strava_activity_id=1), SimpleNamespace(strava_activity_id=2)]

    async def review(db, user, ride_metric, plan, **_kwargs):
        seen.append(plan)
        await crud.upsert_training_plan(db, user.id, [*plan, {"date": f"2026-07-0{len(seen)}"}])

    monkeypatch.setattr(ride_ingest, "apply_ride_plan_matches", matched)
    monkeypatch.setattr(ride_ingest, "review_matched_ride_and_adapt", review)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        planned = (await crud.get_training_plan(db, user_id)).plan
        await ride_ingest.ingest_rides(
            db, user, [_ride(1, "2026-06-01"), _ride(2, "2026-06-02")], ftp=250.0, policy=ride_ingest.ANALYSIS
        )

    assert seen == [planned, [*planned, {"date": "2026-07-01"}]]


def test_only_the_ingest_builds_the_load_chain_for_the_doors_moved_so_far():
    """Stage 1: the sync, the analysis and a .fit upload. The two history imports
    (routers/strava.py, routers/intervals.py) follow in stage 2."""
    import ast
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    builders = set()
    for path in [*backend.glob("routers/*.py"), *backend.glob("services/*.py")]:
        for call in ast.walk(ast.parse(path.read_text())):
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "build_ride_metrics_chain":
                builders.add(str(path.relative_to(backend)))
    assert builders == {"services/ride_ingest.py", "routers/strava.py", "routers/intervals.py"}


def _user_of(auth_headers) -> str:
    return decode_token(auth_headers["Authorization"].split(" ", 1)[1])
