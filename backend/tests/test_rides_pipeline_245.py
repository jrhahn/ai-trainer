"""A ride that arrives through any door marks the summary and the status badge
stale (ai-trainer-ops#245).

The pipeline graph had five nodes, but only the plan and the assessment were
ever notified:
- Nothing notified ``ride_match``, so the badge's edge to it was dead.
- Three import doors cleared the summary by hand, past the graph, and none
  cleared the badge.
- The Strava history import cleared nothing.
- The activity analysis wrote the assessment without telling anyone.

A new source node, ``rides``, is now notified wherever ride rows are written,
and the assessment has one writer, which notifies.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import crud
from auth import decode_token
from services import (
    activity_sync,
    assessment_pipeline,
    plan_pipeline,
    ride_ingest,
    rides_pipeline,
    summary_pipeline,
)
from services.pipeline_graph import graph
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user

BACKEND = Path(__file__).resolve().parents[1]
PRODUCTION = sorted(
    path
    for path in BACKEND.rglob("*.py")
    if not {"tests", ".venv"} & set(path.relative_to(BACKEND).parts)
)
assert plan_pipeline  # imported for the nodes it registers


# --- the graph -------------------------------------------------------------


def _graph_notifications(function: ast.AST, pipeline_name: str | None) -> set[str]:
    """The nodes a function notifies through the graph itself."""
    nodes = set()
    for call in ast.walk(function):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "notify_changed"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in ("graph", "pipeline_graph")
            and call.args
        ):
            first = call.args[0]
            if isinstance(first, ast.Constant):
                nodes.add(first.value)
            elif isinstance(first, ast.Name) and first.id == "PIPELINE_NAME" and pipeline_name:
                nodes.add(pipeline_name)
    return nodes


def _notified_nodes() -> set[str]:
    """Every node that some production code notifies from outside its own module.

    In each module, a function notifies a node when it calls the graph's
    ``notify_changed`` for it, or calls another function of the module that
    does: ``assessment_pipeline.record`` through ``notify_changed``,
    ``plan_pipeline.commit_plan_updates`` directly. A node counts once another
    module calls one of those functions. A wrapper nobody calls notifies nobody.
    """
    trees = {path.stem: ast.parse(path.read_text()) for path in PRODUCTION}
    notifiers: dict[tuple[str, str], set[str]] = {}
    for stem, tree in trees.items():
        pipeline_name = next(
            (
                stmt.value.value
                for stmt in tree.body
                if isinstance(stmt, ast.Assign)
                and [t.id for t in stmt.targets if isinstance(t, ast.Name)] == ["PIPELINE_NAME"]
                and isinstance(stmt.value, ast.Constant)
            ),
            None,
        )
        functions = {
            f.name: f for f in tree.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        nodes = {name: _graph_notifications(f, pipeline_name) for name, f in functions.items()}
        calls = {
            name: {c.func.id for c in ast.walk(f) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            & functions.keys()
            for name, f in functions.items()
        }
        grew = True
        while grew:
            grew = False
            for name in functions:
                for callee in calls[name]:
                    if not nodes[callee] <= nodes[name]:
                        nodes[name] |= nodes[callee]
                        grew = True
        notifiers.update({(stem, name): found for name, found in nodes.items() if found})

    notified: set[str] = set()
    for stem, tree in trees.items():
        for call in ast.walk(tree):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id != stem
            ):
                notified |= notifiers.get((call.func.value.id, call.func.attr), set())
    return notified


def test_every_node_something_depends_on_is_notified():
    depended_on = {dep for node in graph._nodes.values() for dep in node.depends_on}
    assert depended_on, "the graph has no edges: the pipeline modules were not imported"
    assert depended_on - _notified_nodes() == set()


def test_a_ride_reaches_the_summary_and_the_badge():
    assert {"summary", "training_status"} <= set(graph.downstream_of(rides_pipeline.PIPELINE_NAME))


def _calls_in_production(attribute_chain: str) -> list[str]:
    owner, name = attribute_chain.rsplit(".", 1)
    found = []
    for path in PRODUCTION:
        for call in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == name
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == owner
            ):
                found.append(str(path.relative_to(BACKEND)))
    return found


@pytest.mark.parametrize("call", ["summary_pipeline.invalidate", "status_pipeline.invalidate"])
def test_only_the_graph_clears_the_summary_and_the_badge(call):
    assert _calls_in_production(call) == []


def test_the_assessment_has_one_writer():
    assert set(_calls_in_production("crud.upsert_rider_assessment")) == {"services/assessment_pipeline.py"}


# --- every door --------------------------------------------------------------


async def _dashboard_from_before(user_id: str) -> None:
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(db, user_id, estimated_ftp=250, login_summary="Before the ride.")
        await crud.set_training_status(db, user_id, label="On track", tone="positive", rationale="Before.")
        await db.commit()


async def _dashboard(user_id: str) -> tuple[str | None, str | None]:
    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
    return assessment.login_summary, assessment.training_status_label


def _user_of(auth_headers) -> str:
    return decode_token(auth_headers["Authorization"].split(" ", 1)[1])


@pytest.mark.asyncio
async def test_the_background_sync_of_a_ride_older_than_the_newest(monkeypatch):
    """A ride that arrives late. The newest ride stays the same, so the
    last-ride feedback is not rewritten and the assessment notifies nobody: only
    the rides node stands between this ride and a stale dashboard."""
    user_id = await _create_user(email="sync-245@example.com", strava=True, strava_cursor=100)
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=99, activity_date="2026-06-12",
            sport_type="cycling", duration_seconds=3600, tss=60.0,
        )
        await db.commit()
    await _dashboard_from_before(user_id)

    async def fetch(*_args, **_kwargs):
        return [{
            "id": 101, "name": "Morning Ride", "type": "Ride", "sport_type": "Ride",
            "start_date": "2026-06-10T06:00:00Z", "start_date_local": "2026-06-10T08:00:00",
            "elapsed_time": 3600,
        }]

    async def streams(*_args, **_kwargs):
        times = list(range(0, 3601, 60))
        return {"watts": {"data": [210] * len(times)}, "time": {"data": times}}

    async def nothing(*_args, **_kwargs):
        return {}

    async def review(*_args, **_kwargs):
        return "ok", []

    async def learn(*_args, **_kwargs):
        return None

    monkeypatch.setattr(activity_sync, "fetch_recent_strava_activities", fetch)
    monkeypatch.setattr(activity_sync, "fetch_strava_activity_streams", streams)
    monkeypatch.setattr(activity_sync, "enrich_activity_weather", nothing)
    monkeypatch.setattr(ride_ingest, "review_matched_ride_and_adapt", review)
    monkeypatch.setattr(ride_ingest, "learn_from_completed_workouts", learn)
    # Marking the matched plan day completed changes the plan, which tells the
    # summary and the badge by itself. Left out, so the test sees the ride alone.
    monkeypatch.setattr(ride_ingest, "mark_matched_days_completed", learn)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await activity_sync.sync_strava_for_user(db, user)
        await db.commit()

    assert result.imported == 1
    assert await _dashboard(user_id) == (None, None)


@pytest.mark.asyncio
async def test_the_intervals_history_import(auth_headers, monkeypatch):
    import routers.intervals as intervals_router

    user_id = _user_of(auth_headers)
    await _dashboard_from_before(user_id)

    async def activities(*_args, **_kwargs):
        return [{"id": "i245", "name": "Evening Ride", "type": "Ride",
                 "start_date_local": "2026-06-02T18:00:00", "moving_time": 240}]

    async def detail(*_args, **_kwargs):
        return {"average_watts": 210}

    async def streams(*_args, **_kwargs):
        return {"watts": [200, 220, 210, 230], "time": [0, 60, 120, 180]}

    monkeypatch.setattr(intervals_router, "fetch_recent_activities", activities)
    monkeypatch.setattr(intervals_router, "fetch_activity_detail", detail)
    monkeypatch.setattr(intervals_router, "fetch_activity_streams", streams)

    await intervals_router.run_intervals_import(
        user_id=user_id, api_key="secret", athlete_id="0",
        oldest=date(2026, 6, 1), newest=date(2026, 6, 7), ftp=280,
    )

    assert await _dashboard(user_id) == (None, None)


@pytest.mark.asyncio
async def test_the_strava_history_import(auth_headers, monkeypatch):
    import routers.strava as strava_router
    from tests.test_strava import ImportFlowHttpClient

    user_id = _user_of(auth_headers)
    await _dashboard_from_before(user_id)
    monkeypatch.setattr(strava_router.httpx, "AsyncClient", ImportFlowHttpClient)

    await strava_router._run_import_background(
        user_id=user_id, access_token="tok", ftp=250.0, after_ts=0, replace_existing=False,
    )

    assert strava_router._import_progress[user_id]["imported"] == 2
    assert await _dashboard(user_id) == (None, None)


@pytest.mark.asyncio
async def test_a_fit_upload(client, auth_headers, mock_ai_service, monkeypatch):
    import fitparse

    from tests.test_fit_history_upload_157 import _FitFile

    monkeypatch.setattr(fitparse, "FitFile", _FitFile)
    user_id = _user_of(auth_headers)
    await _dashboard_from_before(user_id)

    response = await client.post(
        "/api/v1/users/me/upload-fit/bulk",
        headers=auth_headers,
        files=[("files", ("30.fit", b"30", "application/octet-stream"))],
    )

    assert response.status_code == 200 and response.json()["imported"] == 1, response.text
    assert await _dashboard(user_id) == (None, None)


@pytest.mark.asyncio
async def test_a_session_logged_by_hand(client, auth_headers):
    user_id = _user_of(auth_headers)
    await _dashboard_from_before(user_id)

    response = await client.post(
        "/api/v1/users/me/activities",
        headers=auth_headers,
        json={"date": "2026-06-09", "sport": "cycling", "durationMinutes": 60,
              "perceivedEffort": 3, "notes": ""},
    )

    assert response.status_code == 201, response.text
    assert await _dashboard(user_id) == (None, None)


@pytest.mark.asyncio
async def test_the_analysis_keeps_its_own_summary_and_clears_the_badge(
    client, auth_headers, mock_ai_service, monkeypatch
):
    user_id = _user_of(auth_headers)
    await _dashboard_from_before(user_id)
    told = []
    real = rides_pipeline.notify_changed

    async def spy(db, user):
        told.append(user.id)
        await real(db, user)

    monkeypatch.setattr(rides_pipeline, "notify_changed", spy)
    mock_ai_service["analyse_strava_activities"].return_value = {
        **mock_ai_service["analyse_strava_activities"].return_value,
        "loginSummary": "Written by the analysis.",
    }

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [{
            "id": 1, "name": "Ride", "type": "Ride", "distance": 50000,
            "movingTime": 3600, "elapsedTime": 3700, "totalElevationGain": 500,
            "startDate": "2026-04-10T08:00:00Z", "averageWatts": 220,
        }]},
    )

    assert response.status_code == 200, response.text
    assert await _dashboard(user_id) == ("Written by the analysis.", None)
    # The assessment's notification alone would clear the badge too; the rides
    # it stored are told to the rides node as well.
    assert user_id in told


# --- the summary's own write ---------------------------------------------------


@pytest.mark.asyncio
async def test_writing_the_login_summary_keeps_the_ride_insights_and_the_zones(monkeypatch):
    """Every write of the login summary erased both: upsert_rider_assessment set
    them from its arguments, and the summary's write passes neither."""
    user_id = await _create_user(email="summary-keeps-245@example.com")
    zones = {"zone2": {"low": 120, "high": 140}}
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await assessment_pipeline.record(
            db, user, estimated_ftp=250, hr_zones=zones, ride_insights="Steady endurance."
        )
        await db.commit()
    monkeypatch.setattr(
        summary_pipeline.ai_service, "generate_login_summary", AsyncMock(return_value="A new summary.")
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert await summary_pipeline.regenerate(db, user, provider="gemini") == "A new summary."
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
    assert assessment.login_summary == "A new summary."
    assert assessment.ride_insights == "Steady endurance."
    assert assessment.hr_zones == zones
