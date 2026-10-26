"""Async SQLAlchemy engine and session dependency."""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# The work a session owes once its transaction is resolved, committed or rolled
# back, kept on the session itself (ai-trainer-ops#304). A service that has such
# work puts it there, and the request's session dependency runs it: so this
# module needs to know nothing above it.
_SETTLE_KEY = "settle_after_transaction"


def settle_after_transaction(
    session: AsyncSession, work: Callable[[AsyncSession], Awaitable[None]]
) -> None:
    """Run ``work(session)`` once this session's transaction is resolved; each
    callable once, however often it is put there."""
    owed = session.info.setdefault(_SETTLE_KEY, [])
    if work not in owed:
        owed.append(work)


async def settle(session: AsyncSession) -> None:
    """Run what the session owes, each once, in the order it was put there."""
    for work in session.info.pop(_SETTLE_KEY, []):
        await work(session)


def make_session_dependency(session_maker: async_sessionmaker[AsyncSession]):
    """Build the per-request session dependency for a given session factory.

    A factory rather than one function so the test suite's override is *this*
    code against the test database, instead of a copy that can drift from it — a
    harness that skips the ``finally`` below cannot see a bug that lives in it.
    """

    async def _get_db():
        """Yield an AsyncSession per request: commit, roll back, then settle up.

        The ``finally`` is where a failed request's LLM spend gets written
        (#560). Usage collected by a block that raised cannot be persisted where
        it was collected — that transaction is on its way to the rollback below
        and would take the rows with it — so it waits on the session until the
        transaction is resolved, which is here, on both paths. token_accounting
        puts that write on the session when it defers the usage (see
        :func:`settle_after_transaction`).
        """
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await settle(session)

    return _get_db


# Every route takes this as ``Depends(get_db, scope="function")``
# (ai-trainer-ops#161). Under FastAPI's default "request" scope a yield
# dependency exits after the response has been sent, so the commit above landed
# after the client already had its answer. A reload straight after saving a
# workout could then read the day as not done, and a background task the route
# started ran before the route's own writes were committed. With "function" the
# commit runs when the route returns, before the response leaves.
# tests/test_session_scope.py keeps every route on it.
get_db = make_session_dependency(async_session_maker)
