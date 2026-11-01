"""The nightly run and the ride review judge their writes with the athlete's
limits applied, as the chat does since #336, and the ride review's writer sees
them (review of 2026-11-01, ai-trainer-ops#338 C5)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import crud
from services import plan_maintenance, plan_pipeline
from tests.conftest import TestSessionLocal
from tests.test_plan_writers_priors import _day, _user_with
from tests.test_ride_review_billing_244 import ACTIVITY, _plan_and_user

SESSION_CAP = {"maxDurationMinutes": 60, "since": "2026-10-31"}


async def _cap_for(user_id: str) -> None:
    async with TestSessionLocal() as db:
        await crud.upsert_availability_constraint(
            db,
            user_id,
            constraint_type="max_session_duration",
            required_workout=SESSION_CAP,
            source="chat",
        )
        await db.commit()


@pytest.fixture
def seen_constraints(monkeypatch):
    seen: list[list[dict] | None] = []
    real = plan_pipeline.update_prior_check

    def spy(prior_check, plan, today=None, constraints=None):
        seen.append(constraints)
        return real(prior_check, plan, today, constraints)

    monkeypatch.setattr(plan_pipeline, "update_prior_check", spy)
    return seen


def _types(constraints) -> list[str]:
    return [c["constraintType"] for c in constraints or []]


@pytest.mark.asyncio
async def test_the_nightly_check_reads_the_athletes_limits(
    monkeypatch, seen_constraints
):
    today = date.today()
    plan = [_day(today - timedelta(days=1), "intervals")] + [
        _day(today + timedelta(days=offset), "endurance") for offset in range(7)
    ]
    user_id = await _user_with(plan)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        user.is_onboarded = True
        await db.commit()
    await _cap_for(user_id)
    profiles: list[dict] = []

    async def adapt(plan, feedback, profile, *args, prior_check=None, **kwargs):
        profiles.append(profile)
        return []

    async def nothing(*args, **kwargs):
        return ""

    async def unchanged(*args, **kwargs):
        return False

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", adapt)
    monkeypatch.setattr(plan_maintenance, "training_weather_context_for_user", nothing)
    monkeypatch.setattr(plan_maintenance, "_week_targets_moved", unchanged)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_maintenance.maintain_user_training_plan(db, user)

    assert [_types(c) for c in seen_constraints] == [["max_session_duration"]]
    assert profiles, "the nightly run never asked the writer"


@pytest.mark.asyncio
async def test_the_ride_review_sees_and_is_judged_with_the_athletes_limits(
    client, auth_headers, mock_ai_service, seen_constraints
):
    user_id, _ = await _plan_and_user(auth_headers)
    await _cap_for(user_id)

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [ACTIVITY]},
    )

    assert response.status_code == 200
    assert [_types(c) for c in seen_constraints] == [["max_session_duration"]]
    profile = mock_ai_service["recommend_next_session"].call_args.kwargs["profile"]
    assert "max_session_duration" in str(profile)
