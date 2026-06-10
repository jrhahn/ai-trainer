"""Small in-process scheduler for recurring backend jobs.

This scheduler is intentionally process-local. The current Docker deployment
runs one backend replica, so an asyncio lock is enough to prevent duplicate
in-process executions. If deployment moves to multiple backend replicas, this
should be backed by a database or external worker lock.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

JobRunner = Callable[[], Awaitable[object]]
DelayFactory = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class ScheduledJob:
    name: str
    run: JobRunner
    next_delay: DelayFactory


@dataclass(slots=True)
class JobRun:
    name: str
    status: str
    duration_ms: int = 0
    error: str = ""


class InProcessScheduler:
    """Register and run recurring jobs in the FastAPI process."""

    def __init__(self, *, sleep: SleepFn = asyncio.sleep) -> None:
        self._sleep = sleep
        self._jobs: dict[str, ScheduledJob] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: list[asyncio.Task] = []

    def register(self, job: ScheduledJob) -> None:
        if job.name in self._jobs:
            raise ValueError(f"Scheduler job already registered: {job.name}")
        self._jobs[job.name] = job
        self._locks[job.name] = asyncio.Lock()
        logger.info("Scheduler registered job=%s", job.name)

    async def run_once(self, name: str) -> JobRun:
        job = self._jobs[name]
        lock = self._locks[name]
        if lock.locked():
            logger.info("Scheduler skipped job=%s reason=already_running", name)
            return JobRun(name=name, status="skipped")

        started = datetime.now(timezone.utc)
        logger.info("Scheduler started job=%s", name)
        async with lock:
            try:
                await job.run()
            except Exception as exc:
                duration_ms = _duration_ms(started)
                logger.warning(
                    "Scheduler failed job=%s duration_ms=%s error=%s",
                    name,
                    duration_ms,
                    exc,
                    exc_info=True,
                )
                return JobRun(
                    name=name,
                    status="failed",
                    duration_ms=duration_ms,
                    error=str(exc),
                )

        duration_ms = _duration_ms(started)
        logger.info("Scheduler succeeded job=%s duration_ms=%s", name, duration_ms)
        return JobRun(name=name, status="success", duration_ms=duration_ms)

    def start(self) -> list[asyncio.Task]:
        if self._tasks:
            return self._tasks
        self._tasks = [
            asyncio.create_task(self._run_loop(job), name=f"scheduler:{job.name}")
            for job in self._jobs.values()
        ]
        return self._tasks

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []

    async def _run_loop(self, job: ScheduledJob) -> None:
        while True:
            delay = max(0.0, float(job.next_delay()))
            logger.info(
                "Scheduler scheduled job=%s delay_seconds=%.0f", job.name, delay
            )
            await self._sleep(delay)
            await self.run_once(job.name)


def _duration_ms(started: datetime) -> int:
    return round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
