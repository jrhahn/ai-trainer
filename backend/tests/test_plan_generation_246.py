"""Plan generation is a service, and the nightly rebuild no longer imports a router
(ai-trainer-ops#246).

``_generate_plan_for_user`` lived in routers/ai.py and raised HTTPException.
plan_maintenance imported it back, the only service in the backend that
imported a router, and caught HTTP errors inside a scheduler job.
services/plan_generation.py raises the domain's errors. The router turns them
into 429, 502 and 503, and the nightly run into a log line.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import crud
from config import settings
from services import ai_service, plan_generation, plan_inputs, plan_maintenance
from services.ai_service import AIRateLimitError, AIResponseFormatError
from services.dates import app_today_iso
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user

SERVICES = Path(__file__).resolve().parents[1] / "services"


@pytest.fixture
def stub_on(monkeypatch):
    """The stub provider, as tests/test_stub_provider.py turns it on."""
    monkeypatch.setattr(settings, "ai_stub_provider", True)
    monkeypatch.setattr(settings, "app_env", "test")


def test_no_service_imports_a_router():
    imports = []
    for path in sorted(SERVICES.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "routers"
            ):
                imports.append(f"{path.name}: from {node.module}")
            elif isinstance(node, ast.Import):
                imports += [
                    f"{path.name}: import {a.name}"
                    for a in node.names
                    if a.name.split(".")[0] == "routers"
                ]
    assert imports == []


def test_the_nightly_run_knows_nothing_of_http():
    source = (SERVICES / "plan_maintenance.py").read_text()
    assert "HTTPException" not in source and "fastapi" not in source


@pytest.mark.asyncio
async def test_a_plan_is_generated_by_the_stub_without_the_app(stub_on):
    user_id = await _create_user(email="generate-246@example.com")

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = await plan_generation.generate_plan_for_user(db, user, "UTC")
        await db.commit()

    assert plan
    async with TestSessionLocal() as db:
        stored = await crud.get_training_plan(db, user_id)
        assert [day["date"] for day in stored.plan] == [day["date"] for day in plan]
        # Counted as one of today's rebuilds.
        assert (
            plan_inputs.rebuilds_left(stored, app_today_iso(timezone_name="UTC"))
            == plan_inputs.DAILY_REBUILDS - 1
        )


@pytest.mark.asyncio
async def test_the_limit_is_checked_before_the_model_is_asked(monkeypatch):
    user_id = await _create_user(email="limit-246@example.com")
    async with TestSessionLocal() as db:
        row = await crud.get_training_plan(db, user_id)
        row.rebuild_day, row.rebuild_count = (
            app_today_iso(timezone_name="UTC"),
            plan_inputs.DAILY_REBUILDS,
        )
        await db.commit()

    async def never(*_args, **_kwargs):
        raise AssertionError("the model was asked")

    monkeypatch.setattr(ai_service, "generate_training_plan", never)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        with pytest.raises(plan_generation.RebuildLimitReached):
            await plan_generation.generate_plan_for_user(db, user, "UTC")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [AIResponseFormatError("no JSON"), []])
async def test_an_answer_that_is_no_plan_fails_the_generation(monkeypatch, answer):
    user_id = await _create_user(email="noplan-246@example.com")

    async def generate(*_args, **_kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(ai_service, "generate_training_plan", generate)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        with pytest.raises(plan_generation.PlanGenerationFailed):
            await plan_generation.generate_plan_for_user(db, user, "UTC")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status"),
    [
        (plan_generation.RebuildLimitReached(), 429),
        (plan_generation.PlanGenerationFailed(), 502),
        (AIRateLimitError("busy"), 503),
    ],
)
async def test_the_route_answers_each_error_with_its_status(
    client, auth_headers, monkeypatch, error, status
):
    async def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(plan_generation, "generate_plan_for_user", fail)

    response = await client.post(
        "/api/v1/ai/generate-plan", headers=auth_headers, json={}
    )

    assert response.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        plan_generation.RebuildLimitReached(),
        plan_generation.PlanGenerationFailed(),
        AIRateLimitError("busy"),
    ],
)
async def test_the_nightly_rebuild_skips_on_each_error(monkeypatch, error):
    user_id = await _create_user(
        email=f"nightly-{type(error).__name__.lower()}-246@example.com"
    )

    async def changed(*_args, **_kwargs):
        return ["ftp"]

    async def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(plan_inputs, "changes_since_generation", changed)
    monkeypatch.setattr(plan_generation, "generate_plan_for_user", fail)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert (
            await plan_maintenance.rebuild_if_inputs_changed(
                db, user, timezone_name="UTC"
            )
            is False
        )
