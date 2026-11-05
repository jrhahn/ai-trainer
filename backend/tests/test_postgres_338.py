"""What only Postgres does: the migrations, and the plan row lock (review of
2026-11-01, ai-trainer-ops#338 C12).

The suite runs on SQLite, where ``FOR UPDATE`` does nothing and no migration
runs: the schema is built from the models. So no test had ever seen
``alembic upgrade head`` build a fresh database, or one plan writer wait for
another (#282). These run against the database ``POSTGRES_TEST_URL`` names,
and are skipped without it. They empty that database: point it at a throwaway
one, as the CI job does.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import crud
import models
from database import Base
from services import plan_pipeline

POSTGRES_URL = os.environ.get("POSTGRES_TEST_URL", "")
BACKEND_DIR = Path(__file__).parent.parent

# The CI job sets REQUIRE_POSTGRES: there a missing URL must fail the run, not
# skip all of it and pass.
if os.environ.get("REQUIRE_POSTGRES") and not POSTGRES_URL:
    raise RuntimeError("REQUIRE_POSTGRES is set, but POSTGRES_TEST_URL is not")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="POSTGRES_TEST_URL names no Postgres database"
)

# What the migrations build that the models do not describe, on purpose:
# - knowledge_chunks is the RAG store, a pgvector table the models leave out;
# - ix_users_email_lower is the case-blind unique email index (#109), an
#   expression index the models cannot declare.
_BY_DESIGN = {
    ("remove_table", "knowledge_chunks"),
    ("remove_index", "ix_knowledge_chunks_topics"),
    ("remove_index", "knowledge_chunks_embedding_idx"),
    ("remove_index", "ix_users_email_lower"),
}
# Columns the models call NOT NULL that the migrations left nullable: six
# found on this test's first run, tightened by 20261104_000001. Keep it empty.
_NULLABLE_DRIFT: set[tuple[str, str]] = set()


def _alembic(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": POSTGRES_URL},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def migrated() -> None:
    """The database emptied and built by the migrations, once per module."""

    async def empty() -> None:
        engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(empty())
    _alembic("upgrade", "head")


def _differences(sync_conn) -> list:
    context = MigrationContext.configure(sync_conn, opts={"compare_type": True})
    return compare_metadata(context, Base.metadata)


def _named(diff) -> tuple:
    """A difference as (kind, name), or (kind, table, column) for a column."""
    if isinstance(diff, list):
        (change,) = diff
        return (change[0], change[2], change[3])
    kind, thing = diff[0], diff[1]
    return (kind, thing.name)


async def test_the_migrations_build_the_schema_the_models_describe(migrated):
    engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        found = {_named(d) for d in await conn.run_sync(_differences)}
    await engine.dispose()

    drift = {("modify_nullable", table, column) for table, column in _NULLABLE_DRIFT}
    assert found == _BY_DESIGN | drift


def test_the_newest_migration_goes_down_and_up_again(migrated):
    _alembic("downgrade", "-1")
    _alembic("upgrade", "head")


async def _a_user_with_a_plan(engine, email: str = "lock@example.com") -> str:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        user = models.User(email=email, hashed_password="x", name="Lock")
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, [])
        await db.commit()
        return user.id


async def _try_to_lock(engine, user_id: str):
    """The plan row for a second writer that waits at most a second."""
    async with async_sessionmaker(engine)() as db:
        await db.execute(text("SET LOCAL lock_timeout = '1s'"))
        return await crud.get_training_plan_for_update(db, user_id)


async def test_a_second_plan_writer_waits_for_the_first(migrated):
    engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
    user_id = await _a_user_with_a_plan(engine)

    async with async_sessionmaker(engine)() as first:
        assert await crud.get_training_plan_for_update(first, user_id) is not None
        # A savepoint ends, the lock does not: it lasts to the outer commit,
        # which is why a model call after a plan write keeps the row locked
        # (review of 2026-11-01 C2).
        async with first.begin_nested():
            await crud.get_training_plan_for_update(first, user_id)
        with pytest.raises(DBAPIError, match="lock"):
            await _try_to_lock(engine, user_id)
        await first.commit()

    assert await _try_to_lock(engine, user_id) is not None
    await engine.dispose()


async def test_releasing_the_plan_lock_lets_a_second_writer_in(migrated):
    """What a ride review now does between its plan write and its narration's
    model call (C2): the second writer no longer waits for the model."""
    engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
    user_id = await _a_user_with_a_plan(engine, "release@example.com")

    async with async_sessionmaker(engine, expire_on_commit=False)() as first:
        await crud.get_training_plan_for_update(first, user_id)
        await plan_pipeline.release_plan_lock(first)
        # The model call would run here.
        assert await _try_to_lock(engine, user_id) is not None
        await first.commit()

    await engine.dispose()


async def test_a_null_timestamp_is_filled_before_its_column_is_tightened(migrated):
    engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
    _alembic("downgrade", "20261101_000002")
    user_id = await _a_user_with_a_plan(engine, "null-timestamps@example.com")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO race_events (id, user_id, date, distance_km, "
                "elevation_m, created_at, updated_at) "
                "VALUES ('race-1', :user, '2027-05-01', 100, 1000, NULL, NULL), "
                "('race-2', :user, '2027-06-01', 100, 1000, '2026-01-01', NULL)"
            ),
            {"user": user_id},
        )
        await conn.execute(
            text(
                "INSERT INTO plan_change_summary (id, user_id, batch_id, source, "
                "summary, recorded_at) "
                "VALUES ('summary-1', :user, 'b', 'nightly_maintenance', 's', NULL)"
            ),
            {"user": user_id},
        )

    _alembic("upgrade", "head")

    async with engine.connect() as conn:
        created, updated = (
            await conn.execute(
                text(
                    "SELECT created_at, updated_at FROM race_events WHERE id = 'race-1'"
                )
            )
        ).one()
        second = (
            await conn.execute(
                text(
                    "SELECT created_at, updated_at FROM race_events WHERE id = 'race-2'"
                )
            )
        ).one()
        recorded = await conn.scalar(
            text("SELECT recorded_at FROM plan_change_summary WHERE id = 'summary-1'")
        )
    await engine.dispose()
    assert created is not None
    assert updated is not None
    # The row's own creation time, not the migration's.
    assert second.updated_at == second.created_at
    assert recorded is not None
