"""20261018_000004: every stored weekly-hours value is cleared (ai-trainer-ops#116)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

import models

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20261018_000004_clear_onboarding_weekly_hours.py"
)


def _run(engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_weekly_hours", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


def _hours(engine) -> list:
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(sa.text("SELECT weekly_hours FROM users ORDER BY id"))]


def test_upgrade_clears_every_value_and_leaves_the_rest_of_the_row(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'hours.db'}")
    models.User.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            models.User.__table__.insert(),
            [
                {"id": "1", "email": "a@x.co", "hashed_password": "h", "weekly_hours": 6},
                {"id": "2", "email": "b@x.co", "hashed_password": "h", "weekly_hours": 9.5},
                {"id": "3", "email": "c@x.co", "hashed_password": "h", "weekly_hours": None},
            ],
        )

    _run(engine, "upgrade")
    _run(engine, "upgrade")

    assert _hours(engine) == [None, None, None]
    with engine.connect() as conn:
        assert [r[0] for r in conn.execute(sa.text("SELECT email FROM users ORDER BY id"))] == [
            "a@x.co",
            "b@x.co",
            "c@x.co",
        ]

    _run(engine, "downgrade")
    assert _hours(engine) == [None, None, None]
