from __future__ import annotations

import asyncio

import pytest

from services.scheduler import InProcessScheduler, ScheduledJob


@pytest.mark.asyncio
async def test_scheduler_run_once_success():
    calls = 0

    async def job():
        nonlocal calls
        calls += 1

    scheduler = InProcessScheduler()
    scheduler.register(ScheduledJob("test-job", job, lambda: 0))

    result = await scheduler.run_once("test-job")

    assert calls == 1
    assert result.status == "success"
    assert result.name == "test-job"


@pytest.mark.asyncio
async def test_scheduler_skips_duplicate_concurrent_run():
    started = asyncio.Event()
    release = asyncio.Event()

    async def job():
        started.set()
        await release.wait()

    scheduler = InProcessScheduler()
    scheduler.register(ScheduledJob("locked-job", job, lambda: 0))

    running = asyncio.create_task(scheduler.run_once("locked-job"))
    await started.wait()
    duplicate = await scheduler.run_once("locked-job")
    release.set()
    first = await running

    assert duplicate.status == "skipped"
    assert first.status == "success"


@pytest.mark.asyncio
async def test_scheduler_run_once_failure_is_reported():
    async def job():
        raise RuntimeError("boom")

    scheduler = InProcessScheduler()
    scheduler.register(ScheduledJob("failing-job", job, lambda: 0))

    result = await scheduler.run_once("failing-job")

    assert result.status == "failed"
    assert result.error == "boom"


@pytest.mark.asyncio
async def test_scheduler_recurring_job_runs_with_fake_sleep():
    calls = 0
    sleeps: list[float] = []

    async def job():
        nonlocal calls
        calls += 1

    async def fake_sleep(delay: float):
        sleeps.append(delay)
        if calls >= 1:
            raise asyncio.CancelledError

    scheduler = InProcessScheduler(sleep=fake_sleep)
    scheduler.register(ScheduledJob("recurring-job", job, lambda: 12.5))
    [task] = scheduler.start()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == 1
    assert sleeps == [12.5, 12.5]
