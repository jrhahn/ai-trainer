"""One loop for every per-athlete job (ai-trainer-ops#252).

The loop over every athlete with a plan, with its result class, its log lines
and its scheduler job, was copied into seven modules. They now name their step
and their schedule in a ``PerUserJob``, and main.py registers each through it.
The log lines and the scheduler's job names are the ones the modules wrote.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

import crud
from services import (
    contradiction_detection,
    experiment_suggestion,
    hypothesis_generation,
    insight_generation,
    open_question_generation,
    plan_maintenance,
    prediction_evaluation,
)
from services.per_user_jobs import PerUserJob, PerUserJobResult
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user

BACKEND = Path(__file__).resolve().parents[1]
JOBS = {
    contradiction_detection: "athlete-contradiction-detection",
    experiment_suggestion: "validation-experiment-suggestion",
    hypothesis_generation: "athlete-hypothesis-generation",
    insight_generation: "athlete-insight-generation",
    open_question_generation: "athlete-open-question-generation",
    plan_maintenance: "daily-plan-maintenance",
    prediction_evaluation: "prediction-evaluation",
}


@pytest.mark.parametrize("module", list(JOBS), ids=lambda m: m.__name__)
def test_the_module_keeps_only_its_step_and_its_schedule(module):
    tree = ast.parse(Path(module.__file__).read_text())
    own = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and (
            node.name.startswith("run_")
            or node.name.endswith("_job")
            or node.name.endswith("Result")
            or node.name.startswith("seconds_until_next_")
        )
    ]
    assert own == []
    assert isinstance(module.JOB, PerUserJob)
    assert module.JOB.name == JOBS[module]


def test_main_registers_each_job_through_it():
    source = (BACKEND / "main.py").read_text()
    for module in JOBS:
        assert (
            f"scheduler.register({module.__name__.split('.')[-1]}.JOB.scheduled(async_session_maker))"
            in source
        )


@pytest.mark.parametrize(
    ("returns", "counts", "expected", "skipped"),
    [
        (2, ("generated",), {"generated": 2}, 0),
        (True, ("updated",), {"updated": 1}, 0),
        ((3, 1), ("evaluated", "generated"), {"evaluated": 3, "generated": 1}, 0),
        (0, ("generated",), {"generated": 0}, 1),
        (False, ("updated",), {"updated": 0}, 1),
    ],
)
@pytest.mark.asyncio
async def test_what_a_step_returns_is_counted(
    returns, counts, expected, skipped, caplog
):
    await _create_user(
        email=f"job-{counts[0]}-{returns}-252@example.com".replace(" ", "")
    )
    seen = []

    async def step(db, user, *, now, timezone_name):
        seen.append((user.id, now, timezone_name))
        return returns

    job = PerUserJob(
        name="test-job", label="Test job", step=step, counts=counts, hour=3
    )
    with caplog.at_level(logging.INFO, logger="services.per_user_jobs"):
        result = await job.run(TestSessionLocal, timezone_name="Europe/Berlin")

    assert result.checked == len(seen) >= 1
    assert {name: getattr(result, name) for name in counts} == {
        k: v * len(seen) for k, v in expected.items()
    }
    assert (result.skipped, result.failed) == (skipped * len(seen), 0)
    assert {zone for _user, _now, zone in seen} == {"Europe/Berlin"}
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0] == "Test job started"
    counted = " ".join(f"{name}={getattr(result, name)}" for name in counts)
    assert messages[-1].startswith(
        f"Test job finished checked={result.checked} {counted} skipped={result.skipped} failed=0 duration_ms="
    )


@pytest.mark.asyncio
async def test_a_failing_athlete_is_counted_and_the_others_go_on(caplog):
    first = await _create_user(email="job-fails-252@example.com")
    calls = []

    async def step(db, user, *, now, timezone_name):
        calls.append(user.id)
        if user.id == first:
            raise RuntimeError("model down")
        return 1

    job = PerUserJob(
        name="test-job", label="Test job", step=step, counts=("generated",), hour=3
    )
    with caplog.at_level(logging.INFO, logger="services.per_user_jobs"):
        result = await job.run(TestSessionLocal)

    assert first in calls and result.failed == 1 and result.generated == len(calls) - 1
    assert f"Test job failed for user_id={first}" in [
        r.getMessage() for r in caplog.records
    ]


@pytest.mark.asyncio
async def test_each_athlete_has_a_session_of_their_own_and_is_committed():
    user_id = await _create_user(email="job-commit-252@example.com")

    async def step(db, user, *, now, timezone_name):
        user.name = "Written by the job"
        return 1

    await PerUserJob(
        name="test-job", label="Test job", step=step, counts=("generated",), hour=3
    ).run(TestSessionLocal)

    async with TestSessionLocal() as db:
        assert (await crud.get_user_by_id(db, user_id)).name == "Written by the job"


def test_a_count_the_job_does_not_keep_is_no_attribute():
    result = PerUserJobResult(("generated",))

    assert result.generated == 0
    with pytest.raises(AttributeError):
        result.flagged  # noqa: B018


@pytest.mark.parametrize(
    ("module", "now", "seconds"),
    [
        # Daily at its hour in the app's zone, weekly on its weekday.
        (plan_maintenance, "2026-06-09T23:00:00+00:00", 60 * 60),
        (insight_generation, "2026-06-08T01:00:00+00:00", 2 * 60 * 60),
    ],
)
def test_the_job_knows_its_own_schedule(module, now, seconds):
    from datetime import datetime

    zone = "Europe/Berlin" if module is plan_maintenance else "UTC"
    assert (
        module.JOB.seconds_until_next_run(
            datetime.fromisoformat(now), timezone_name=zone
        )
        == seconds
    )


@pytest.mark.asyncio
async def test_the_scheduled_job_runs_in_the_apps_zone(monkeypatch):
    """What main.py registers: the job's name, its run in the app's zone, and the
    delay to its next hour."""
    from config import settings

    monkeypatch.setattr(settings, "app_timezone", "Europe/Berlin")
    zones = []

    async def step(db, user, *, now, timezone_name):
        zones.append(timezone_name)
        return 1

    await _create_user(email="job-scheduled-252@example.com")
    job = PerUserJob(
        name="test-job",
        label="Test job",
        step=step,
        counts=("generated",),
        hour=3,
        weekday=0,
    )
    scheduled = job.scheduled(TestSessionLocal)

    result = await scheduled.run()

    assert scheduled.name == "test-job"
    assert result.generated == len(zones) >= 1 and set(zones) == {"Europe/Berlin"}
    assert 0 <= scheduled.next_delay() <= 7 * 24 * 60 * 60


def test_a_time_without_a_zone_is_read_as_utc():
    from datetime import datetime

    from services.scheduler import (
        seconds_until_next_daily_run,
        seconds_until_next_weekly_run,
    )

    naive = datetime(2026, 6, 8, 1, 0)  # a Monday, 01:00
    assert (
        seconds_until_next_daily_run(naive, timezone_name="UTC", hour=3) == 2 * 60 * 60
    )
    assert (
        seconds_until_next_weekly_run(naive, timezone_name="UTC", weekday=0, hour=3)
        == 2 * 60 * 60
    )
