"""What is left of a history import once its process is gone (ai-trainer-ops#284).

The live progress of a running import is in the importing router's memory
(``progress_store``). A restart in the middle of one emptied it, and the
progress question then answered "idle", as if nothing had been asked for. Each
import now keeps a row here: written as it starts, updated in the transaction
of every batch it stores, closed when it ends. The app's start marks the rows
still "running" as interrupted; the app runs as one process
(``docs/multi_replica.md``), so none of them can still be running.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import models

logger = logging.getLogger(__name__)

INTERVALS = "intervals"

# What the athlete reads when an import ends early, instead of the exception's
# own text. Fixed sentences, because the interface translates them by their
# exact wording (frontend ``i18n/producers.test.ts`` reads this block).
IMPORT_ERRORS = {
    "rejected": "intervals.icu no longer accepts your API key. Connect intervals.icu again in Settings.",
    "unavailable": "intervals.icu did not answer. Try the import again in a few minutes.",
    "failed": "The import stopped with an error. Try it again; if it fails again, let us know.",
    "interrupted": "The import was interrupted, most likely by an update of the app. What arrived is kept; start the import again for the rest.",
}

# Why one activity of an import could not be stored, when the reason is an
# error of ours rather than one the import recognised.
ACTIVITY_FAILED = "Could not be processed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def start(db: AsyncSession, user_id: str, source: str) -> str:
    """Record a new running import and return its id. It replaces the
    athlete's earlier imports from ``source``: only the latest one is asked
    about."""
    await db.execute(
        delete(models.ImportJob).where(
            models.ImportJob.user_id == user_id, models.ImportJob.source == source
        )
    )
    now = _now()
    job = models.ImportJob(
        user_id=user_id, source=source, status="running", started_at=now, updated_at=now
    )
    db.add(job)
    await db.flush()
    return job.id


async def record(
    db: AsyncSession, job_id: str, *, status: str = "running", **counts
) -> None:
    """Write how far the import is (``total``, ``processed``, ``imported``,
    ``skipped``, ``failed_activities``, ``error``), and close it when ``status``
    is no longer running. The caller commits."""
    values = {**counts, "status": status, "updated_at": _now()}
    if status != "running":
        values["finished_at"] = values["updated_at"]
    await db.execute(
        update(models.ImportJob).where(models.ImportJob.id == job_id).values(**values)
    )


async def latest(
    db: AsyncSession, user_id: str, source: str
) -> models.ImportJob | None:
    return await db.scalar(
        select(models.ImportJob)
        .where(models.ImportJob.user_id == user_id, models.ImportJob.source == source)
        .order_by(models.ImportJob.started_at.desc())
        .limit(1)
    )


async def mark_interrupted(session_maker: async_sessionmaker[AsyncSession]) -> int:
    """Close the imports a previous process left running, as interrupted.

    Called once as the app starts, before it serves a request. A failure here
    must not keep the app from starting: the progress question reads a row
    still "running" as interrupted anyway.
    """
    try:
        async with session_maker() as db:
            now = _now()
            result = await db.execute(
                update(models.ImportJob)
                .where(models.ImportJob.status == "running")
                .values(
                    status="interrupted",
                    error=IMPORT_ERRORS["interrupted"],
                    updated_at=now,
                    finished_at=now,
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Could not mark the interrupted imports", exc_info=True)
        return 0
    if result.rowcount:
        logger.info(
            "Marked %s import(s) a restart cut short as interrupted", result.rowcount
        )
    return result.rowcount or 0
