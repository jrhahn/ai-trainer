"""The guard on a now-destructive operation (#640).

``prune_orphaned_chunks`` used to be scoped to ``seed:`` rows, because a second,
partially-fetched source existed alongside them. With Semantic Scholar gone the
markdown files are the entire corpus, so the prune deletes *anything* they do not
produce — which is what clears the 188 paper rows, and what would clear the whole
table if the file list ever came back empty.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts import ingest_cycling_science as ing


@pytest.mark.asyncio
async def test_pruning_against_an_empty_corpus_is_refused():
    """An unreadable knowledge/ must not read as "delete everything"."""
    session_maker = MagicMock(side_effect=AssertionError("must not reach the database"))

    with pytest.raises(ValueError, match="empty corpus"):
        await ing.prune_orphaned_chunks(session_maker, set())

    session_maker.assert_not_called()


def test_the_knowledge_files_produce_the_ids_the_prune_keeps():
    """The prune's keep-set is only as good as this: a stem mismatch deletes."""
    ids = ing.seed_source_ids()

    assert ids, "no corpus files found, which would make the prune refuse"
    assert all(source_id.startswith("seed:") for source_id in ids)
    # sources.md is bookkeeping (#630), so it must not be in the keep set — and
    # must therefore be pruned if it is still stored.
    assert "seed:sources" not in ids
    assert "seed:power_zones" in ids
