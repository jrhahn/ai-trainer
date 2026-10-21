"""20261021_000001: one workout log per session, not per day (ai-trainer-ops#174).

Runs the migration on ``workout_logs`` as production had it: ``slot`` from #496,
but still the initial schema's ``UNIQUE (user_id, date)``. Before the migration a
planned session (slot 0) cannot be logged next to an extra one (slot 100); after
it, it can, and one session still holds one log. Also idempotency, and the
downgrade, which stops while a day holds two logs.
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
    / "20261021_000001_workout_log_unique_per_session.py"
)

# workout_logs before the migration, reduced to the columns an insert needs, with
# the initial schema's unnamed per-day constraint and its date index.
OLD_TABLE = """
CREATE TABLE workout_logs (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    date VARCHAR(20) NOT NULL,
    actual_duration_minutes INTEGER NOT NULL,
    perceived_effort INTEGER NOT NULL,
    notes TEXT NOT NULL,
    completed_at VARCHAR(50) NOT NULL,
    slot INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, date)
)
"""


def _run(engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_workout_log_slot", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


def _engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'workout_logs.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text(OLD_TABLE))
        conn.execute(sa.text("CREATE INDEX ix_workout_logs_date ON workout_logs (date)"))
    return engine


def _log(engine, log_id: str, slot: int, date: str = "2026-10-20") -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO workout_logs (id, user_id, date, actual_duration_minutes,"
                " perceived_effort, notes, completed_at, slot)"
                " VALUES (:id, 'u1', :date, 60, 3, '', :date, :slot)"
            ),
            {"id": log_id, "date": date, "slot": slot},
        )


def _unique_keys(engine) -> list[list[str]]:
    return [c["column_names"] for c in sa.inspect(engine).get_unique_constraints("workout_logs")]


def test_upgrade_lets_a_planned_session_join_an_extra_one(tmp_path):
    engine = _engine(tmp_path)
    _log(engine, "extra", slot=100)
    with pytest.raises(sa.exc.IntegrityError):
        _log(engine, "planned", slot=0)  # the 500 of #174

    _run(engine, "upgrade")

    _log(engine, "planned", slot=0)
    with pytest.raises(sa.exc.IntegrityError):
        _log(engine, "planned-again", slot=0)


def test_upgrade_keeps_the_rows_and_the_date_index(tmp_path):
    engine = _engine(tmp_path)
    _log(engine, "a", slot=0)
    _log(engine, "b", slot=0, date="2026-10-21")

    _run(engine, "upgrade")

    with engine.connect() as conn:
        ids = [r[0] for r in conn.execute(sa.text("SELECT id FROM workout_logs ORDER BY id"))]
    assert ids == ["a", "b"]
    assert "ix_workout_logs_date" in {i["name"] for i in sa.inspect(engine).get_indexes("workout_logs")}


def test_upgrade_is_idempotent(tmp_path):
    engine = _engine(tmp_path)

    _run(engine, "upgrade")
    _run(engine, "upgrade")

    assert _unique_keys(engine) == [["user_id", "date", "slot"]]


def test_downgrade_makes_a_day_unique_again(tmp_path):
    engine = _engine(tmp_path)
    _run(engine, "upgrade")
    _log(engine, "a", slot=0)

    _run(engine, "downgrade")

    assert _unique_keys(engine) == [["user_id", "date"]]
    with pytest.raises(sa.exc.IntegrityError):
        _log(engine, "b", slot=100)


def test_downgrade_stops_while_a_day_holds_two_logs(tmp_path):
    engine = _engine(tmp_path)
    _run(engine, "upgrade")
    _log(engine, "planned", slot=0)
    _log(engine, "extra", slot=100)

    with pytest.raises(RuntimeError, match="1 day"):
        _run(engine, "downgrade")

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM workout_logs")).scalar() == 2
    assert _unique_keys(engine) == [["user_id", "date", "slot"]]


def test_the_model_declares_the_key_the_migration_creates():
    # The tests build their schema from the model, so this is what keeps them
    # seeing production's key.
    keys = [
        list(c.columns.keys())
        for c in models.WorkoutLog.__table__.constraints
        if isinstance(c, sa.UniqueConstraint)
    ]
    assert keys == [["user_id", "date", "slot"]]
