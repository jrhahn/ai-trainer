"""20261101_000001: chat_messages gets an index on (user_id, timestamp)
(review of 2026-11-01, ai-trainer-ops#338 C16)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

import models

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20261101_000001_chat_messages_user_index.py"
)
INDEX = "ix_chat_messages_user_timestamp"


def _run(engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_chat_index", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


@pytest.fixture()
def engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE chat_messages (id VARCHAR(36) PRIMARY KEY, "
                "user_id VARCHAR(36) NOT NULL, timestamp VARCHAR(50) NOT NULL)"
            )
        )
    return engine


def _indexes(engine) -> dict[str, list[str]]:
    return {
        i["name"]: i["column_names"]
        for i in sa.inspect(engine).get_indexes("chat_messages")
    }


def test_the_index_is_made_once_and_dropped_on_the_way_down(engine):
    _run(engine, "upgrade")
    _run(engine, "upgrade")  # a second run finds it there and does nothing
    assert _indexes(engine) == {INDEX: ["user_id", "timestamp"]}

    _run(engine, "downgrade")
    assert _indexes(engine) == {}


def test_the_model_declares_the_same_index():
    """The test database is built from the models, not the migrations."""
    (index,) = [i for i in models.ChatMessage.__table__.indexes if i.name == INDEX]
    assert [c.name for c in index.columns] == ["user_id", "timestamp"]
