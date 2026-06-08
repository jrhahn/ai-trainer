"""Tests that verify the Alembic migration chain is well-formed.

These tests do not require a live database – they inspect the migration scripts
on disk using Alembic's ScriptDirectory API.  The primary goal is to catch the
class of bug fixed in commit d4e5c91 where a migration's down_revision pointed
to an older revision instead of the current head, producing a branched graph
with two heads and making ``alembic upgrade head`` fail.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).parent.parent  # …/backend/


def _get_script_dir() -> ScriptDirectory:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "sqlite+aiosqlite:///./test.db")
    return ScriptDirectory.from_config(cfg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migration_chain_has_exactly_one_head():
    """Alembic must see exactly one head revision.

    Multiple heads cause ``alembic upgrade head`` to fail with:
        ERROR [alembic.util.messaging] Multiple head revisions are present for
        given argument 'head'; please specify a specific target revision.
    """
    script = _get_script_dir()
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Expected exactly 1 Alembic head, found {len(heads)}: {heads}. "
        "A migration's down_revision likely points to the wrong parent."
    )


def test_migration_chain_is_linear():
    """Every revision must have at most one parent (no branch points).

    A branching migration graph means two separate revision lines were created
    from the same ancestor without being merged, which again produces multiple
    heads.
    """
    script = _get_script_dir()
    for rev in script.walk_revisions():
        if rev.down_revision is None:
            continue  # base revision
        parents = (
            list(rev.down_revision)
            if isinstance(rev.down_revision, tuple)
            else [rev.down_revision]
        )
        assert len(parents) == 1, (
            f"Revision {rev.revision!r} has {len(parents)} parents "
            f"({parents!r}). The migration chain must be linear."
        )


def test_all_down_revisions_reference_existing_revisions():
    """Every down_revision value must point to a revision that actually exists.

    A typo or stale reference in down_revision can silently create a dangling
    migration that Alembic cannot connect to the rest of the chain.
    """
    script = _get_script_dir()
    all_revisions = {rev.revision for rev in script.walk_revisions()}

    for rev in script.walk_revisions():
        if rev.down_revision is None:
            continue
        parents = (
            list(rev.down_revision)
            if isinstance(rev.down_revision, tuple)
            else [rev.down_revision]
        )
        for parent in parents:
            assert parent in all_revisions, (
                f"Revision {rev.revision!r} references unknown parent "
                f"{parent!r} in down_revision."
            )


def test_migration_revisions_are_unique():
    """Each revision ID must be unique within the migration history."""
    script = _get_script_dir()
    rev_ids: list[str] = [rev.revision for rev in script.walk_revisions()]
    assert len(rev_ids) == len(set(rev_ids)), (
        f"Duplicate revision IDs detected: "
        f"{[r for r in rev_ids if rev_ids.count(r) > 1]}"
    )


def test_intervals_auto_sync_has_followup_migration():
    """The Intervals auto-sync column must not be added only by an edited old revision.

    Databases that already applied ``20260608_000001`` need a later revision to
    add ``users.intervals_auto_sync_enabled``.
    """
    script = _get_script_dir()
    revision = script.get_revision("20260608_000002")
    assert revision is not None
    assert revision.down_revision == "20260608_000001"
