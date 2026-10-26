"""20261018_000002: emails become case-insensitive (ai-trainer-ops#109).

Exercises the migration on a real ``users`` table: the lowercase backfill, the
unique index on ``lower(email)`` that refuses a case variant (the race the
application-level check cannot close), idempotency, and the abort when two rows
already differ only in case.
"""

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
    / "20261018_000002_email_case_insensitive.py"
)


def _run(engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_email_case", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


def _engine(tmp_path, rows: list[dict]):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'email_case.db'}")
    models.User.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(models.User.__table__.insert(), rows)
    return engine


def _emails(engine) -> list[str]:
    with engine.connect() as conn:
        return [
            r[0] for r in conn.execute(sa.text("SELECT email FROM users ORDER BY id"))
        ]


def test_upgrade_lowercases_rows_and_refuses_a_case_variant(tmp_path):
    engine = _engine(
        tmp_path,
        [
            {"id": "1", "email": "Foo@Example.de", "hashed_password": "h"},
            {"id": "2", "email": "bar@x.co", "hashed_password": "h"},
        ],
    )

    _run(engine, "upgrade")

    assert _emails(engine) == ["foo@example.de", "bar@x.co"]
    with engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                models.User.__table__.insert(),
                {"id": "3", "email": "FOO@example.de", "hashed_password": "h"},
            )


def test_upgrade_is_idempotent(tmp_path):
    engine = _engine(
        tmp_path, [{"id": "1", "email": "Mixed@Case.de", "hashed_password": "h"}]
    )

    _run(engine, "upgrade")
    _run(engine, "upgrade")  # must not raise "index already exists"

    assert _emails(engine) == ["mixed@case.de"]


def test_upgrade_aborts_when_two_rows_differ_only_in_case(tmp_path):
    engine = _engine(
        tmp_path,
        [
            {"id": "1", "email": "Dup@x.co", "hashed_password": "h"},
            {"id": "2", "email": "dup@x.co", "hashed_password": "h"},
        ],
    )

    with pytest.raises(RuntimeError, match="differ only in case"):
        _run(engine, "upgrade")
