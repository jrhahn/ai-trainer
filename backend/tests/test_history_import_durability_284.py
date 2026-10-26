"""A history import keeps what it stored when the app restarts, and says it was
cut short (ai-trainer-ops#284).

The intervals.icu import held everything it fetched in memory and wrote it in
one transaction at the end, and its progress lived in the process alone. A
restart in the middle of a 24-month import (a deploy is one) lost every
activity fetched so far, and the progress question then answered "idle": the
athlete never learned the import had stopped. An error reached the athlete as
the exception's own text.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select

import crud
import models
from auth import decode_token
from routers import intervals as intervals_router
from services import import_jobs
from services.intervals_service import IntervalsAuthError
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user

DAYS = ["2026-06-02", "2026-06-04", "2026-06-06", "2026-06-08", "2026-06-10"]


def _serve(
    monkeypatch,
    raising: dict[str, BaseException] | None = None,
    *,
    newest_first: bool = False,
) -> None:
    """intervals.icu lists a ride on each of DAYS, an hour at 220 W, and the
    import stores two at a time. Fetching an id in ``raising`` raises."""
    raising = raising or {}

    async def listing(*_args, **_kwargs):
        listed = [
            {
                "id": f"i-{n}",
                "name": f"Ride {n}",
                "type": "Ride",
                "start_date_local": f"{day}T08:00:00",
                "moving_time": 3600 + 600 * n,
            }
            for n, day in enumerate(DAYS)
        ]
        return listed[::-1] if newest_first else listed

    async def detail(_api_key, raw_id):
        if raw_id in raising:
            raise raising[raw_id]
        return {"average_watts": 220}

    async def streams(_api_key, raw_id):
        # Rides of different lengths, so the order they are chained in shows.
        times = list(range(0, 3601 + 600 * int(raw_id[2:]), 60))
        return {"watts": [220] * len(times), "time": times}

    monkeypatch.setattr(intervals_router, "fetch_recent_activities", listing)
    monkeypatch.setattr(intervals_router, "fetch_activity_detail", detail)
    monkeypatch.setattr(intervals_router, "fetch_activity_streams", streams)
    monkeypatch.setattr(intervals_router, "IMPORT_BATCH", 2)


async def _import(user_id: str) -> None:
    await intervals_router.run_intervals_import(
        user_id=user_id,
        api_key="secret",
        athlete_id="0",
        oldest=date(2026, 6, 1),
        newest=date(2026, 6, 30),
        ftp=280,
    )


async def _restart_while_fetching_the_fifth(monkeypatch, user_id: str) -> None:
    """The process ends in the third batch; the first two are stored."""
    _serve(monkeypatch, raising={"i-4": asyncio.CancelledError()})
    with pytest.raises(asyncio.CancelledError):
        await _import(user_id)
    # The new process starts with nothing in memory.
    intervals_router._intervals_import_progress.pop(user_id, None)


async def _stored(user_id: str) -> int:
    async with TestSessionLocal() as db:
        return await db.scalar(
            select(func.count())
            .select_from(models.RideMetric)
            .where(models.RideMetric.user_id == user_id)
        )


async def _jobs(user_id: str) -> list[models.ImportJob]:
    async with TestSessionLocal() as db:
        return list(
            await db.scalars(
                select(models.ImportJob).where(models.ImportJob.user_id == user_id)
            )
        )


def _user(auth_headers) -> str:
    return decode_token(auth_headers["Authorization"].split(" ", 1)[1])


async def _progress(client, auth_headers) -> dict:
    response = await client.get(
        "/api/v1/intervals/import-progress", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_a_restart_keeps_the_batches_stored_before_it(auth_headers, monkeypatch):
    user_id = _user(auth_headers)

    await _restart_while_fetching_the_fifth(monkeypatch, user_id)

    assert await _stored(user_id) == 4


@pytest.mark.asyncio
async def test_after_the_restart_the_progress_says_interrupted(
    client, auth_headers, monkeypatch
):
    user_id = _user(auth_headers)
    await _restart_while_fetching_the_fifth(monkeypatch, user_id)

    assert await import_jobs.mark_interrupted(TestSessionLocal) == 1
    body = await _progress(client, auth_headers)

    assert body["status"] == "interrupted"
    assert (body["total"], body["processed"], body["imported"]) == (5, 4, 4)
    assert body["error"] == import_jobs.IMPORT_ERRORS["interrupted"]
    [job] = await _jobs(user_id)
    assert job.status == "interrupted"
    assert job.finished_at is not None


@pytest.mark.asyncio
async def test_a_row_left_running_reads_as_interrupted_even_unmarked(
    client, auth_headers, monkeypatch
):
    """The start's marking is best effort; the answer does not depend on it."""
    user_id = _user(auth_headers)
    await _restart_while_fetching_the_fifth(monkeypatch, user_id)

    assert (await _progress(client, auth_headers))["status"] == "interrupted"


@pytest.mark.asyncio
async def test_the_start_closes_only_imports_still_running():
    user_id = await _create_user(email="import-jobs-284@example.com")
    async with TestSessionLocal() as db:
        running = await import_jobs.start(db, user_id, import_jobs.INTERVALS)
        finished = await import_jobs.start(db, user_id, "strava")
        await import_jobs.record(db, finished, status="done", imported=3)
        await db.commit()

    assert await import_jobs.mark_interrupted(TestSessionLocal) == 1

    statuses = {job.id: job.status for job in await _jobs(user_id)}
    assert statuses == {running: "interrupted", finished: "done"}


@pytest.mark.asyncio
async def test_the_start_survives_a_database_that_does_not_answer():
    def no_database():
        raise OSError("connection refused")

    assert await import_jobs.mark_interrupted(no_database) == 0


@pytest.mark.asyncio
async def test_starting_again_replaces_the_interrupted_import(
    client, auth_headers, monkeypatch
):
    user_id = _user(auth_headers)
    await _restart_while_fetching_the_fifth(monkeypatch, user_id)

    _serve(monkeypatch)
    await _import(user_id)

    assert (await _progress(client, auth_headers))["status"] == "done"
    assert await _stored(user_id) == 5
    assert [
        (job.status, job.imported, job.processed) for job in await _jobs(user_id)
    ] == [("done", 5, 5)]
    # Once the finished import has left memory, nothing is reported as cut short.
    intervals_router._intervals_import_progress.pop(user_id, None)
    assert (await _progress(client, auth_headers))["status"] == "idle"


@pytest.mark.asyncio
async def test_a_refused_key_in_the_middle_keeps_what_was_stored(
    client, auth_headers, monkeypatch
):
    user_id = _user(auth_headers)
    _serve(
        monkeypatch,
        raising={"i-2": IntervalsAuthError("Intervals.icu credentials were rejected")},
    )

    await _import(user_id)

    body = await _progress(client, auth_headers)
    assert body["status"] == "error"
    assert body["error"] == import_jobs.IMPORT_ERRORS["rejected"]
    assert body["imported"] == 2
    assert await _stored(user_id) == 2
    [job] = await _jobs(user_id)
    assert (job.status, job.imported, job.error) == (
        "error",
        2,
        import_jobs.IMPORT_ERRORS["rejected"],
    )


@pytest.mark.asyncio
async def test_an_activity_that_fails_on_our_side_gets_a_fixed_reason(
    auth_headers, monkeypatch
):
    user_id = _user(auth_headers)
    _serve(
        monkeypatch,
        raising={"i-1": RuntimeError("KeyError 'icu_weighted_avg_watts' in mapper")},
    )

    await _import(user_id)

    progress = intervals_router._intervals_import_progress[user_id]
    assert progress["status"] == "done"
    assert [f["reason"] for f in progress["failed_activities"]] == [
        import_jobs.ACTIVITY_FAILED
    ]
    assert (progress["imported"], progress["skipped"]) == (4, 1)


@pytest.mark.asyncio
async def test_storing_in_batches_leaves_the_chain_one_import_would(monkeypatch):
    """Every batch replays the whole chain, so the stored loads add up the same.
    Newest first, so a later batch brings older rides than the ones stored."""
    in_batches = await _create_user(email="batches-284@example.com")
    at_once = await _create_user(email="at-once-284@example.com")
    _serve(monkeypatch, newest_first=True)
    await _import(in_batches)
    monkeypatch.setattr(intervals_router, "IMPORT_BATCH", 25)
    await _import(at_once)

    async def chain(user_id: str):
        async with TestSessionLocal() as db:
            rows = await crud.get_all_ride_metrics_ordered(db, user_id)
        return [
            (r.activity_date, r.tss, r.ctl_after, r.atl_after, r.tsb_after)
            for r in rows
        ]

    assert len(await chain(in_batches)) == 5
    assert await chain(in_batches) == await chain(at_once)


MIGRATIONS = Path(__file__).parent.parent / "alembic" / "versions"


def _migration(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_migration_{name}", MIGRATIONS / name
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, module, step: str) -> None:
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, step)()


def test_the_migration_turns_the_strava_table_into_import_jobs(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    models.User.__table__.create(engine)
    _run(engine, _migration("20260427_000001_add_strava_import_jobs.py"), "upgrade")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO strava_import_jobs (id, user_id, status, started_at, updated_at)"
                " VALUES ('old', 'u-1', 'done', '2026-05-01', '2026-05-01')"
            )
        )
    migration = _migration("20261025_000001_import_jobs.py")

    _run(engine, migration, "upgrade")
    _run(engine, migration, "upgrade")  # a second run finds nothing to do

    inspector = sa.inspect(engine)
    assert "strava_import_jobs" not in inspector.get_table_names()
    assert {c["name"] for c in inspector.get_columns("import_jobs")} >= {
        c.name for c in models.ImportJob.__table__.columns
    }
    assert {i["name"] for i in inspector.get_indexes("import_jobs")} == {
        "ix_import_jobs_user_id"
    }
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT id, source FROM import_jobs")).all() == [
            ("old", "strava")
        ]

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO import_jobs (id, user_id, source, status, started_at, updated_at)"
                " VALUES ('new', 'u-1', 'intervals', 'running', '2026-10-25', '2026-10-25')"
            )
        )
    _run(engine, migration, "downgrade")

    inspector = sa.inspect(engine)
    assert "import_jobs" not in inspector.get_table_names()
    assert {i["name"] for i in inspector.get_indexes("strava_import_jobs")} == {
        "ix_strava_import_jobs_user_id"
    }
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT id FROM strava_import_jobs")).all() == [
            ("old",)
        ]


@pytest.mark.asyncio
async def test_an_early_end_reports_this_run_and_not_the_last(
    auth_headers, monkeypatch
):
    user_id = _user(auth_headers)
    _serve(monkeypatch)
    await _import(user_id)

    async def refused(*_args, **_kwargs):
        raise IntervalsAuthError("Intervals.icu credentials were rejected")

    monkeypatch.setattr(intervals_router, "fetch_recent_activities", refused)
    await _import(user_id)

    progress = intervals_router._intervals_import_progress[user_id]
    assert (progress["status"], progress["total"], progress["imported"]) == (
        "error",
        0,
        0,
    )
