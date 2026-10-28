"""One adapt path for the nightly run and the adaptation after a session
(ai-trainer-ops#246).

Before, the router adapted the plan after a session that signalled fatigue,
illness or pain with a copy of the nightly run's chain, and the two copies had
drifted: the nightly run never saw what the athlete and the coach agreed in the
chat, and the adaptation after a session never saw the rides' load or the
athlete model, nor did its narration. The owner's answer: one adapt function in
services/plan_maintenance.py that both call, with the same context, before the
friends test. The router keeps only the trigger.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import timedelta

import pytest

import crud
import models
from domain.passwords import hash_password
from services import (
    coach_summary,
    freshness_allocation,
    plan_maintenance,
    token_accounting,
)
from services.dates import app_today
from tests.conftest import TestSessionLocal

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _iso(days: int) -> str:
    return (app_today() + timedelta(days=days)).isoformat()


def _day(date: str) -> dict:
    return {
        "date": date,
        "workoutType": "endurance",
        "title": f"Ride {date}",
        "description": "Endurance ride",
        "durationMinutes": 60,
    }


async def _athlete(email: str) -> str:
    """An onboarded athlete who missed yesterday's ride, which gives the night a
    reason to run, and who agreed something with the coach for this week."""
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Test Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
            # Every day a training day, so the rest-day guard keeps the change
            # whatever weekday the test runs on.
            training_days=[
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ],
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(
            db, user.id, [_day(_iso(-1)), _day(_iso(1)), _day(_iso(3))]
        )
        await crud.upsert_rider_assessment(
            db, user.id, estimated_ftp=262, rider_type="climber"
        )
        # Days inside an arrangement are kept at commit time, so it starts after
        # the day the tests change.
        await crud.create_plan_commitment(
            db,
            user.id,
            start_date=_iso(4),
            end_date=_iso(6),
            text="Keep the days before the trip easy",
        )
        await db.commit()
        return user.id


@pytest.fixture
def sections(monkeypatch):
    """Markers for the sections built elsewhere, so the test reads where each goes."""

    async def weather(*_args, **_kwargs):
        return "<weather>"

    async def athlete_model(*_args, **_kwargs):
        return "<athlete model>"

    monkeypatch.setattr(
        plan_maintenance,
        "ride_metrics_context_section",
        lambda *_args, **_kwargs: "<training load>",
    )
    monkeypatch.setattr(plan_maintenance, "training_weather_context_for_user", weather)
    monkeypatch.setattr(
        freshness_allocation, "athlete_model_section_for_user", athlete_model
    )


@pytest.fixture
def adapt_calls(monkeypatch) -> list[tuple[tuple, dict, str | None]]:
    """Each adapt call's arguments, and the usage scope it was billed to."""
    calls: list[tuple[tuple, dict, str | None]] = []

    async def adapt(*args, **kwargs):
        calls.append((args, kwargs, token_accounting.current_source()))
        return [{**_day(_iso(1)), "workoutType": "recovery", "title": "Recovery Spin"}]

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", adapt)
    return calls


@pytest.fixture
def narrations(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def narrate(*_args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(coach_summary, "narrate_plan_changes", narrate)
    return calls


async def _plan(user_id: str) -> list[dict]:
    async with TestSessionLocal() as db:
        return list((await crud.get_training_plan(db, user_id)).plan)


@pytest.mark.asyncio
async def test_both_triggers_send_the_model_the_same_context(
    sections, adapt_calls, narrations
):
    night_id = await _athlete("night-246@example.com")
    session_id = await _athlete("session-246@example.com")
    feedback = {"perceivedEffort": 9, "notes": "Legs were empty"}

    async with TestSessionLocal() as db:
        night = await crud.get_user_by_id(db, night_id)
        assert await plan_maintenance.maintain_user_training_plan(
            db, night, timezone_name="UTC"
        )
        session = await crud.get_user_by_id(db, session_id)
        await plan_maintenance.adapt_after_feedback(
            db, session, await _plan(session_id), feedback, timezone_name="UTC"
        )
        await db.commit()

    for user_id in (night_id, session_id):
        assert {d["date"]: d["workoutType"] for d in await _plan(user_id)}[
            _iso(1)
        ] == "recovery"
    (night_args, night_kwargs, night_source), (session_args, session_kwargs, _) = (
        adapt_calls
    )
    assert night_args[1] == [] and session_args[1] == [feedback]
    # The night bills its call to its own step, as before; the adaptation after
    # a session runs inside the rating request's scope.
    assert night_source == "step:plan-maintenance"
    assert set(night_kwargs) == set(session_kwargs)
    for kwargs in (night_kwargs, session_kwargs):
        assert kwargs["rider_assessment"]["riderType"] == "climber"
        # What the night gains: the arrangement agreed in the chat.
        assert "Keep the days before the trip easy" in kwargs["plan_commitments"]
        # What the adaptation after a session gains: the rides' load and the
        # athlete model.
        assert kwargs["metrics_history_section"] == "<training load>"
        assert kwargs["athlete_model_section"] == "<athlete model>"
        assert kwargs["weather_context_section"] == "<weather>"
    # Both narrate with the load and the weather the adaptation acted on.
    assert [n["source"] for n in narrations] == ["nightly_maintenance", "auto_adapt"]
    for narration in narrations:
        assert narration["training_load_section"] == "<training load>"
        assert narration["weather_context_section"] == "<weather>"
        # The plan as committed, so the message is checked against it
        # (ai-trainer-ops#316).
        committed = {d["date"]: d["workoutType"] for d in narration["plan"]}
        assert committed[_iso(1)] == "recovery"


@pytest.mark.asyncio
async def test_the_nights_narration_is_billed_to_its_own_step(
    sections, adapt_calls, monkeypatch
):
    """The night's usage scope now holds the whole adaptation, the narration
    included. Scopes nest, and a call is recorded in the innermost one, so the
    narration's call stays billed to step:coach-narration, as before."""
    from services import ai_service

    user_id = await _athlete("billing-246@example.com")
    sources: list[str] = []

    async def summarize(*_args, **_kwargs):
        sources.append(token_accounting.current_source())
        return {"summary": ""}

    monkeypatch.setattr(ai_service, "summarize_plan_changes", summarize)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert await plan_maintenance.maintain_user_training_plan(
            db, user, timezone_name="UTC"
        )
        await db.commit()

    assert sources == ["step:coach-narration"]


@pytest.mark.asyncio
async def test_a_failed_narration_keeps_the_nightly_plan(
    sections, adapt_calls, monkeypatch
):
    """The adaptation after a session had its narration in a savepoint (#281);
    the night now has it too, so a narration that fails in the database no
    longer undoes the night's plan."""
    from tests.test_best_effort_savepoints_281 import _fail_a_flush

    user_id = await _athlete("narration-246@example.com")

    async def narration_breaks(db, *_args, **_kwargs):
        await _fail_a_flush(db)

    monkeypatch.setattr(coach_summary, "narrate_plan_changes", narration_breaks)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert await plan_maintenance.maintain_user_training_plan(
            db, user, timezone_name="UTC"
        )
        await db.commit()

    assert {d["date"]: d["workoutType"] for d in await _plan(user_id)}[
        _iso(1)
    ] == "recovery"


@pytest.mark.asyncio
async def test_a_failed_adaptation_after_a_session_is_not_raised(
    sections, monkeypatch, caplog
):
    user_id = await _athlete("failed-246@example.com")

    async def adapt_breaks(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(
        plan_maintenance.ai_service, "adapt_training_plan", adapt_breaks
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_maintenance.adapt_after_feedback(
            db, user, await _plan(user_id), {"perceivedEffort": 9}
        )
        await db.commit()

    assert "Auto-adaptation after flagged workout failed" in caplog.text
    assert [d["workoutType"] for d in await _plan(user_id)] == ["endurance"] * 3


def test_one_place_asks_the_model_to_adapt_the_plan():
    """The router keeps the trigger; the adaptation itself exists once."""
    callers = []
    for path in sorted(BACKEND.rglob("*.py")):
        relative = path.relative_to(BACKEND)
        if relative.parts[0] in {"tests", ".venv"}:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "adapt_training_plan"
            ):
                callers.append(str(relative))
    assert callers == ["services/plan_maintenance.py"]
