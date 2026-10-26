"""One loop for the jobs that run a step for every athlete with a plan (ai-trainer-ops#252).

The loop was copied into seven modules, each with a result class of its own:
- read every athlete with a plan;
- give each one a session of their own and read them fresh;
- run the step and count what it did;
- write one line at the end.

A module now names its step, what the step counts and when it runs; this runs it.
The log lines and the scheduler's job names are the ones the modules wrote.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
from config import settings
from services.scheduler import (
    ScheduledJob,
    seconds_until_next_daily_run,
    seconds_until_next_weekly_run,
)
from services.token_accounting import flushing_session

logger = logging.getLogger(__name__)

# What a step did for one athlete: a count, whether it changed anything, or one
# count for each name in ``PerUserJob.counts``.
StepResult = int | bool | tuple[int, ...]
Step = Callable[..., Awaitable[StepResult]]


class PerUserJobResult:
    """Athletes checked, skipped and failed, and what the step counted, by name."""

    def __init__(self, counts: tuple[str, ...]) -> None:
        self.checked = 0
        self.skipped = 0
        self.failed = 0
        self.counts = dict.fromkeys(counts, 0)

    def __getattr__(self, name: str) -> int:
        # result.generated, result.flagged and the like, as the modules' own
        # result classes had them.
        try:
            return self.__dict__["counts"][name]
        except KeyError:
            raise AttributeError(name) from None


def _counted(value: StepResult, names: tuple[str, ...]) -> dict[str, int]:
    if isinstance(value, tuple):
        return dict(zip(names, (int(v or 0) for v in value)))
    return {names[0]: int(value or 0)}


@dataclass(frozen=True)
class PerUserJob:
    """A step for every athlete with a plan, once a day or once a week.

    ``step(db, user, *, now, timezone_name)`` is called with the athlete read
    fresh in a session of their own, which is committed after it. An athlete
    the step counted nothing for is skipped. One that fails is counted as
    failed, logged, and does not stop the others.
    """

    name: str
    label: str
    step: Step
    counts: tuple[str, ...]
    hour: int
    weekday: int | None = None

    def seconds_until_next_run(
        self, now: datetime | None = None, *, timezone_name: str | None = None
    ) -> float:
        if self.weekday is None:
            return seconds_until_next_daily_run(
                now, timezone_name=timezone_name, hour=self.hour
            )
        return seconds_until_next_weekly_run(
            now, timezone_name=timezone_name, weekday=self.weekday, hour=self.hour
        )

    async def run(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        now: datetime | None = None,
        timezone_name: str | None = None,
    ) -> PerUserJobResult:
        started = datetime.now(timezone.utc)
        result = PerUserJobResult(self.counts)
        logger.info("%s started", self.label)

        async with session_factory() as db:
            users = await crud.get_users_with_training_plans(db)

        for user in users:
            result.checked += 1
            try:
                async with flushing_session(session_factory) as db:
                    fresh_user = await crud.get_user_by_id(db, user.id)
                    if fresh_user is None:
                        result.skipped += 1
                        continue
                    counted = _counted(
                        await self.step(
                            db, fresh_user, now=now, timezone_name=timezone_name
                        ),
                        self.counts,
                    )
                    if any(counted.values()):
                        for name, count in counted.items():
                            result.counts[name] += count
                    else:
                        result.skipped += 1
                    await db.commit()
            except Exception:
                result.failed += 1
                logger.warning(
                    "%s failed for user_id=%s", self.label, user.id, exc_info=True
                )

        duration_ms = round(
            (datetime.now(timezone.utc) - started).total_seconds() * 1000
        )
        logger.info(
            "%s finished checked=%s "
            + "".join(f"{name}=%s " for name in self.counts)
            + "skipped=%s failed=%s duration_ms=%s",
            self.label,
            result.checked,
            *result.counts.values(),
            result.skipped,
            result.failed,
            duration_ms,
        )
        return result

    def scheduled(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> ScheduledJob:
        """The scheduler's job: the run in the app's zone, at its hour."""

        async def _run() -> object:
            return await self.run(session_factory, timezone_name=settings.app_timezone)

        return ScheduledJob(
            name=self.name,
            run=_run,
            next_delay=lambda: self.seconds_until_next_run(
                timezone_name=settings.app_timezone
            ),
        )
