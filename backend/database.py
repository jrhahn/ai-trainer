"""Async SQLAlchemy engine and session dependency."""

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
        transaction is resolved, which is here, on both paths.
        """
        # Imported lazily: services.token_accounting reaches models, and models
        # reaches this module for Base.
        from services.token_accounting import flush_deferred_usage

        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await flush_deferred_usage(session)

    return _get_db


get_db = make_session_dependency(async_session_maker)
