"""The work a session owes once its transaction is resolved (ai-trainer-ops#304).

database.py's request dependency wrote a failed request's LLM spend (#560) by
importing token_accounting inside the dependency: the database layer reaching
up into the services. The session now carries the work it owes, and the
dependency runs whatever is there.
"""

from __future__ import annotations

import pytest

import database
from services import token_accounting
from tests.conftest import TestSessionLocal


@pytest.mark.asyncio
async def test_what_a_session_owes_runs_once_each_in_order():
    ran: list[str] = []

    async def first(session):
        ran.append("first")

    async def second(session):
        ran.append("second")

    async with TestSessionLocal() as session:
        database.settle_after_transaction(session, first)
        database.settle_after_transaction(session, second)
        database.settle_after_transaction(session, first)

        await database.settle(session)
        await database.settle(session)

    assert ran == ["first", "second"]


@pytest.mark.asyncio
async def test_the_request_dependency_settles_after_a_commit_and_after_a_rollback():
    seen: list[bool] = []

    async def owed(session):
        seen.append(session.in_transaction())

    dependency = database.make_session_dependency(TestSessionLocal)
    for fails in (False, True):
        gen = dependency()
        session = await anext(gen)
        database.settle_after_transaction(session, owed)
        if fails:
            with pytest.raises(RuntimeError):
                await gen.athrow(RuntimeError("the route failed"))
        else:
            with pytest.raises(StopAsyncIteration):
                await anext(gen)

    # Run once per request, after its transaction was resolved either way.
    assert seen == [False, False]


@pytest.mark.asyncio
async def test_deferred_usage_puts_its_flush_on_the_session():
    collected = token_accounting.Collection()
    collected.usage.total = 10

    async with TestSessionLocal() as session:
        token_accounting._defer_usage(session, "user-1", "api:x", collected)
        token_accounting._defer_usage(session, "user-1", "api:y", collected)

        assert session.info[database._SETTLE_KEY] == [
            token_accounting.flush_deferred_usage
        ]
        # Nothing deferred, nothing owed.
        async with TestSessionLocal() as other:
            token_accounting._defer_usage(
                other, "user-1", "api:z", token_accounting.Collection()
            )
            assert database._SETTLE_KEY not in other.info
