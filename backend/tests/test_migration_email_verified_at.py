"""20261015_000003: every account that exists already counts as confirmed (app #687).

It was made before there was anything to confirm with, so the nightly pruning of
unconfirmed accounts must never reach it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20261015_000003_add_email_verified_at.py"
)


def _run(engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_email_verified_at", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


@pytest.fixture()
def engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255), "
                "created_at DATETIME)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, created_at) VALUES "
                "('a', 'early@example.com', '2026-05-01 08:00:00'), "
                "('b', 'undated@example.com', NULL)"
            )
        )
    return engine


def _confirmed(engine) -> dict[str, object]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT id, email_verified_at FROM users")).all()
    return dict(rows)


def test_existing_accounts_count_as_confirmed(engine):
    _run(engine, "upgrade")

    confirmed = _confirmed(engine)
    assert str(confirmed["a"]).startswith("2026-05-01 08:00:00")
    assert confirmed["b"] is not None


def test_upgrade_is_idempotent_and_downgrade_drops_the_column(engine):
    _run(engine, "upgrade")
    _run(engine, "upgrade")
    _run(engine, "downgrade")

    columns = {c["name"] for c in sa.inspect(engine).get_columns("users")}
    assert "email_verified_at" not in columns
