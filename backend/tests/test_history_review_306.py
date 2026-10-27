"""Connecting intervals.icu imports the last weeks, and the plan is rebuilt
from them once (ai-trainer-ops#306).

The connect used to check the key and nothing more. Then it imported six weeks
of rides and the coach looked at them once. The owner's answer on #136,
2026-10-26, (c): the connect rebuilds the whole plan at once from the new
history, through the generation the night uses, and the coach says so in the
chat. The rebuild records what the plan was built from, so the night does not
rebuild it again: one change, told once.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

import crud
import models
from auth import decode_token
from database import async_session_maker
from routers import intervals as intervals_router
from services import (
    ai_service,
    coach_summary,
    history_review,
    plan_inputs,
    plan_maintenance,
    plan_pipeline,
    rest_days,
)
from services.dates import app_today
from services.intervals_service import intervals_activity_id


def _user_id(auth_headers) -> str:
    return decode_token(auth_headers["Authorization"].split(" ", 1)[1])


def _activity(raw_id: str, day: str, name: str = "Ride") -> dict:
    return {
        "id": raw_id,
        "name": name,
        "type": "Ride",
        "start_date_local": f"{day}T07:30:00",
        "moving_time": 3600,
        "average_watts": 200,
    }


@pytest.fixture
def intervals_rides(monkeypatch):
    """intervals.icu lists these rides and serves their detail and streams."""
    listed: list[dict] = []

    async def listing(*_args, **_kwargs):
        return list(listed)

    async def detail(*_args, **_kwargs):
        return {"average_watts": 200, "icu_training_load": 40}

    async def streams(*_args, **_kwargs):
        return {"watts": [190, 200, 210, 200], "time": [0, 60, 120, 180]}

    monkeypatch.setattr(intervals_router, "fetch_recent_activities", listing)
    monkeypatch.setattr(intervals_router, "fetch_activity_detail", detail)
    monkeypatch.setattr(intervals_router, "fetch_activity_streams", streams)
    return listed


def _days() -> list[dict]:
    today = app_today()
    return [
        {
            "date": (today + timedelta(days=offset)).isoformat(),
            "workoutType": "endurance",
            "title": "Endurance Ride",
            "description": "Steady aerobic",
            "durationMinutes": 90,
        }
        for offset in (2, 3)
    ]


@pytest.fixture
def model(monkeypatch):
    """The plan generation and the narration, recorded. The generator answers
    the plan it is given, or the stored one unchanged."""
    calls: dict[str, list] = {"generate": [], "narrate": [], "progress": []}
    answer: dict = {"plan": _days()}

    async def generate(profile, **kwargs):
        calls["generate"].append({"profile": profile, **kwargs})
        user_id = next(iter(intervals_router._intervals_import_progress))
        calls["progress"].append(
            intervals_router._intervals_import_progress[user_id]["status"]
        )
        if isinstance(answer.get("raise"), Exception):
            raise answer["raise"]
        return [dict(day) for day in answer["plan"]]

    async def narrate(changes, profile, **kwargs):
        calls["narrate"].append(
            {
                "run_context": kwargs["run_context"],
                "reply_language": kwargs.get("reply_language"),
                "dates": [change["date"] for change in changes],
            }
        )
        if isinstance(answer.get("narration"), Exception):
            raise answer["narration"]
        return {
            "summary": "I rebuilt your plan from your last weeks and eased Thursday.",
            "days": [],
        }

    monkeypatch.setattr(ai_service, "generate_training_plan", generate)
    monkeypatch.setattr(coach_summary.ai_service, "summarize_plan_changes", narrate)
    return calls, answer


async def _plan(user_id: str, *, recorded: bool = False) -> list[dict]:
    """The athlete's plan; *recorded* also keeps what it was built from, as a
    generation does."""
    plan = _days()
    async with async_session_maker() as db:
        await crud.upsert_training_plan(db, user_id, plan)
        if recorded:
            await plan_inputs.record(db, await crud.get_user_by_id(db, user_id))
        await db.commit()
    return plan


async def _import(user_id: str, **kwargs) -> dict:
    intervals_router._intervals_import_progress.clear()
    today = app_today()
    await intervals_router.run_intervals_import(
        user_id=user_id,
        api_key="secret",
        athlete_id="0",
        oldest=today - timedelta(days=42),
        newest=today + timedelta(days=1),
        ftp=250,
        **kwargs,
    )
    return intervals_router._intervals_import_progress[user_id]


async def _chat(user_id: str) -> list[models.ChatMessage]:
    async with async_session_maker() as db:
        return list(
            await db.scalars(
                select(models.ChatMessage).where(models.ChatMessage.user_id == user_id)
            )
        )


@pytest.fixture
def started_imports(client, auth_headers, monkeypatch):
    """A connected account whose imports are recorded rather than run."""
    started: list[dict] = []

    async def accepts(*_args, **_kwargs):
        return [], 0

    async def record(**kwargs):
        started.append(kwargs)

    monkeypatch.setattr(intervals_router, "fetch_activity_listing", accepts)
    monkeypatch.setattr(intervals_router, "run_intervals_import", record)
    intervals_router._intervals_import_progress.clear()
    return started


async def _connect(client, auth_headers) -> None:
    response = await client.put(
        "/api/v1/intervals/connection",
        headers=auth_headers,
        json={"apiKey": "k" * 25, "athleteId": "0"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_connect_asks_for_six_weeks_and_a_rebuild(
    client, auth_headers, started_imports
):
    await _connect(client, auth_headers)

    response = await client.post(
        "/api/v1/intervals/import-history?days=42&analyse=true",
        headers={
            **auth_headers,
            "X-App-Language": "de",
            "X-App-Timezone": "Europe/Berlin",
        },
    )

    assert response.status_code == 202
    assert response.json() == {"status": "started"}
    [job] = started_imports
    assert job["oldest"] == app_today() - timedelta(days=42)
    assert job["newest"] == app_today() + timedelta(days=1)
    assert job["analyse"] is True
    assert job["language"] == "German"
    assert job["timezone_name"] == "Europe/Berlin"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query, length", [("days=3", 7), ("days=5000", 730), ("months=2", 60), ("", 90)]
)
async def test_the_window_is_a_week_to_two_years_and_months_still_work(
    client, auth_headers, started_imports, query, length
):
    await _connect(client, auth_headers)

    await client.post(f"/api/v1/intervals/import-history?{query}", headers=auth_headers)

    [job] = started_imports
    assert job["oldest"] == app_today() - timedelta(days=length)
    assert job["analyse"] is False


@pytest.mark.asyncio
async def test_a_second_import_waits_while_the_plan_is_rebuilt(
    client, auth_headers, started_imports
):
    await _connect(client, auth_headers)
    intervals_router._intervals_import_progress[_user_id(auth_headers)] = {
        "status": "analysing",
        "total": 5,
    }

    response = await client.post(
        "/api/v1/intervals/import-history?days=42&analyse=true", headers=auth_headers
    )

    assert response.json() == {"status": "already_running"}
    assert started_imports == []


@pytest.mark.asyncio
async def test_the_connect_rebuilds_the_plan_once_and_tells_what_changed(
    auth_headers, intervals_rides, model
):
    user_id = _user_id(auth_headers)
    plan = await _plan(user_id)
    calls, answer = model
    today = app_today()
    intervals_rides += [
        _activity("i100", (today - timedelta(days=20)).isoformat()),
        _activity("i200", (today - timedelta(days=1)).isoformat(), "Newest"),
        # No date: the import counts it as failed, and the rebuild goes on without it.
        {"id": "i300", "name": "No date", "type": "Ride"},
    ]
    answer["plan"] = [
        plan[0],
        {**plan[1], "workoutType": "recovery", "title": "Easy spin"},
    ]

    progress = await _import(user_id, analyse=True, language="English")

    assert progress["status"] == "done"
    assert progress["imported"] == 2
    assert progress["skipped"] == 1
    assert progress["analysed"] is True
    assert progress["plan_changes"] == 1
    # One generation, while the progress said so, from the stored history.
    assert len(calls["generate"]) == 1
    assert calls["progress"] == ["analysing"]
    newest = (today - timedelta(days=1)).isoformat()
    assert newest in calls["generate"][0]["metrics_history_section"]
    async with async_session_maker() as db:
        user = await crud.get_user_by_id(db, user_id)
        row = await crud.get_training_plan(db, user_id)
        sources = set(
            await db.scalars(
                select(models.PlanDayHistory.source).where(
                    models.PlanDayHistory.user_id == user_id
                )
            )
        )
        assert user.intervals_analysis_complete is True
        assert user.last_intervals_activity_id == intervals_activity_id("i200")
    assert {d["date"]: d["workoutType"] for d in row.plan}[plan[1]["date"]] == (
        "recovery"
    )
    assert sources == {"history_rebuild"}
    # The dashboard's one sentence names the history, as for a night's rebuild.
    assert plan_inputs.last_rebuild(row)["reasons"] == ["history"]
    # Told in the chat, framed as this rebuild, in the interface language.
    [narration] = calls["narrate"]
    assert narration["run_context"] == coach_summary._RUN_CONTEXT["history_rebuild"]
    assert narration["reply_language"] == "English"
    assert narration["dates"] == [plan[1]["date"]]
    assert [m.content for m in await _chat(user_id)] == [
        "I rebuilt your plan from your last weeks and eased Thursday."
    ]


@pytest.mark.asyncio
async def test_the_night_does_not_rebuild_again_for_the_same_history(
    auth_headers, intervals_rides, model
):
    """One change, told once: not a second rebuild in the morning (#206)."""
    user_id = _user_id(auth_headers)
    await _plan(user_id, recorded=True)
    calls, _ = model
    today = app_today()
    intervals_rides += [
        _activity(f"i{n}", (today - timedelta(days=n)).isoformat())
        for n in range(1, 11)
    ]

    await _import(user_id, analyse=True)
    async with async_session_maker() as db:
        user = await crud.get_user_by_id(db, user_id)
        rebuilt = await plan_maintenance.rebuild_if_inputs_changed(db, user)

    assert rebuilt is False
    assert len(calls["generate"]) == 1


@pytest.mark.asyncio
async def test_without_the_rebuild_the_night_would_rebuild_for_that_history(
    auth_headers, intervals_rides, model
):
    """What the record prevents: ten rides on a plan built from none."""
    user_id = _user_id(auth_headers)
    await _plan(user_id, recorded=True)
    calls, _ = model
    today = app_today()
    intervals_rides += [
        _activity(f"i{n}", (today - timedelta(days=n)).isoformat())
        for n in range(1, 11)
    ]

    await _import(user_id)
    async with async_session_maker() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert await plan_inputs.changes_since_generation(db, user) == ["history"]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["German", "English"])
async def test_a_rebuilt_plan_that_came_out_the_same_says_so(
    auth_headers, intervals_rides, model, language
):
    user_id = _user_id(auth_headers)
    await _plan(user_id)
    calls, _ = model
    intervals_rides.append(_activity("i100", app_today().isoformat()))

    progress = await _import(user_id, analyse=True, language=language)

    assert progress["analysed"] is True
    assert progress["plan_changes"] == 0
    # Nothing changed, so nothing to narrate: the fixed sentence instead.
    assert calls["narrate"] == []
    [message] = await _chat(user_id)
    assert message.role == "assistant"
    assert message.content == history_review.REBUILT_UNCHANGED[language]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["German", "English"])
async def test_a_rebuild_whose_telling_failed_is_still_told(
    auth_headers, intervals_rides, model, language
):
    """Never a silent rebuild (jrhahn/ai-trainer#664)."""
    user_id = _user_id(auth_headers)
    plan = await _plan(user_id)
    _, answer = model
    answer["plan"] = [plan[0], {**plan[1], "workoutType": "recovery"}]
    answer["narration"] = RuntimeError("provider down")
    intervals_rides.append(_activity("i100", app_today().isoformat()))

    progress = await _import(user_id, analyse=True, language=language)

    assert progress["plan_changes"] == 1
    [message] = await _chat(user_id)
    assert message.content == history_review.REBUILT[language]


@pytest.mark.asyncio
async def test_without_a_plan_the_coach_waits_for_onboarding(
    auth_headers, intervals_rides, model
):
    """Onboarding analyses and builds the first plan itself."""
    user_id = _user_id(auth_headers)
    calls, _ = model
    intervals_rides.append(_activity("i100", app_today().isoformat()))

    progress = await _import(user_id, analyse=True)

    assert progress["status"] == "done"
    assert progress["imported"] == 1
    assert progress["analysed"] is False
    assert calls["generate"] == []
    assert await _chat(user_id) == []


async def _use_up_rebuilds(user_id: str) -> None:
    async with async_session_maker() as db:
        row = await crud.get_training_plan(db, user_id)
        row.rebuild_day = app_today().isoformat()
        row.rebuild_count = plan_inputs.DAILY_REBUILDS
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("provider down"), "unparsable", "no rebuilds left"],
    ids=["raises", "unparsable", "limit"],
)
async def test_a_failed_rebuild_keeps_the_rides_and_leaves_the_analysis_to_the_dashboard(
    auth_headers, intervals_rides, model, failure
):
    user_id = _user_id(auth_headers)
    plan = await _plan(user_id)
    calls, answer = model
    if failure == "unparsable":
        answer["raise"] = ai_service.AIResponseFormatError("no plan")
    elif failure == "no rebuilds left":
        await _use_up_rebuilds(user_id)
    else:
        answer["raise"] = failure
    intervals_rides.append(_activity("i100", app_today().isoformat()))

    progress = await _import(user_id, analyse=True)

    assert progress["status"] == "done"
    assert progress["imported"] == 1
    assert progress["analysed"] is False
    if failure == "no rebuilds left":
        # Refused before the model is called.
        assert calls["generate"] == []
    async with async_session_maker() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user.intervals_analysis_complete is False
        assert (await crud.get_training_plan(db, user_id)).plan == plan
    assert await _chat(user_id) == []


@pytest.mark.asyncio
async def test_an_import_that_asked_for_no_rebuild_says_nothing_about_one(
    auth_headers, intervals_rides, model
):
    user_id = _user_id(auth_headers)
    await _plan(user_id)
    calls, _ = model
    intervals_rides.append(_activity("i100", app_today().isoformat()))

    progress = await _import(user_id)

    assert progress["status"] == "done"
    assert "analysed" not in progress
    assert calls["generate"] == []


def test_the_rebuild_is_an_automatic_generation_told_in_the_chat():
    assert "history_rebuild" in coach_summary.NARRATED_SOURCES
    # The training-day guard and the pins hold as for any generation.
    assert "history_rebuild" in rest_days.GUARDED_TRIGGERS
    assert plan_pipeline.PLAN_SOURCES["history_rebuild"].respect_pins is True
    assert plan_pipeline.PLAN_SOURCES["generate"].respect_pins is True


@pytest.fixture
def analysis(monkeypatch):
    """The dashboard's own analysis, answering as the model would."""

    async def analyse(rides, **_kwargs):
        return {
            "estimatedFTP": None,
            "riderType": "allrounder",
            "notes": "Steady aerobic base.",
            "rideInsights": "Mostly endurance.",
            "lastRideFeedback": "Good ride.",
            "loginSummary": "Six weeks in.",
            "planUpdates": [],
        }

    monkeypatch.setattr(ai_service, "analyse_strava_activities", analyse)


@pytest.mark.asyncio
async def test_the_analysis_leaves_a_stored_ride_as_the_import_stored_it(
    client, auth_headers, intervals_rides, analysis
):
    """The dashboard's analysis carries summaries only; storing an imported ride
    again from them lost what its streams gave it (ai-trainer-ops#306)."""
    user_id = _user_id(auth_headers)
    day = app_today().isoformat()
    intervals_rides.append(_activity("i100", day, "Imported"))
    await _import(user_id)
    async with async_session_maker() as db:
        before = await crud.get_ride_metric_by_source(db, user_id, "intervals", "i100")
        kept = (before.tss, before.tss_source, before.perf_signals, before.ride_purpose)

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "source": "intervals",
            "activities": [
                {
                    "id": intervals_activity_id(raw),
                    "externalId": raw,
                    "name": name,
                    "type": "Ride",
                    "distance": 30000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 100,
                    "startDate": f"{day}T07:30:00",
                    "averageWatts": 150,
                }
                for raw, name in (("i100", "Summary only"), ("i300", "New"))
            ],
        },
    )

    assert response.status_code == 200
    async with async_session_maker() as db:
        after = await crud.get_ride_metric_by_source(db, user_id, "intervals", "i100")
        new = await crud.get_ride_metric_by_source(db, user_id, "intervals", "i300")
    assert after.activity_name == "Imported"
    assert (after.tss, after.tss_source, after.perf_signals, after.ride_purpose) == kept
    assert new is not None and new.activity_name == "New"


async def _store_cursor_ride(user_id: str, raw_id: str, day: str) -> None:
    """A ride the sync stored, and the cursor on it."""
    async with async_session_maker() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=intervals_activity_id(raw_id),
            activity_source="intervals",
            external_activity_id=raw_id,
            activity_date=day,
            activity_start_datetime=f"{day}T18:00:00",
            duration_seconds=3600,
        )
        user = await crud.get_user_by_id(db, user_id)
        user.last_intervals_activity_id = intervals_activity_id(raw_id)
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor_day, cursor_after",
    [(0, "i999"), (10, "i200")],
    ids=["later-ride-keeps-it", "earlier-ride-moves-it"],
)
async def test_the_cursor_never_moves_back_in_time(
    auth_headers, intervals_rides, model, cursor_day, cursor_after
):
    """The ids are hashes: the cursor's ride is looked up and kept when it
    started later, as on a second connect (review of #1177)."""
    user_id = _user_id(auth_headers)
    await _plan(user_id)
    today = app_today()
    await _store_cursor_ride(
        user_id, "i999", (today - timedelta(days=cursor_day)).isoformat()
    )
    intervals_rides += [
        _activity("i100", (today - timedelta(days=3)).isoformat()),
        _activity("i200", (today - timedelta(days=1)).isoformat()),
    ]

    progress = await _import(user_id, analyse=True)

    assert progress["analysed"] is True
    async with async_session_maker() as db:
        user = await crud.get_user_by_id(db, user_id)
    assert user.last_intervals_activity_id == intervals_activity_id(cursor_after)


@pytest.mark.asyncio
async def test_no_rebuild_for_an_account_that_is_gone():
    review = await intervals_router._review_history(
        "no-such-user", [], timezone_name=None, language=None
    )
    assert review == history_review.NOT_REVIEWED
