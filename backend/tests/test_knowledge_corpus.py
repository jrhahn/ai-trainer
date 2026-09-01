"""Which files in knowledge/ are corpus, and which only describe it (#630).

`sources.md` is a bibliography. It was ingested like everything else, retrieved
at 0.712 answering a question about VO₂max intervals, and — because a citation
list honestly repeats every term in the vocabulary — earned all three topic tags,
so #627's ranking promoted it for every limiter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.knowledge_corpus import is_corpus_document, parse_front_matter

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"


# ---------------------------------------------------------------------------
# parse_front_matter
# ---------------------------------------------------------------------------


def test_the_marker_never_becomes_corpus_itself():
    """The body is what gets embedded, so the header must not ride along."""
    front, body = parse_front_matter("---\nrag: false\n---\n\n# Title\n\nProse.\n")

    assert front == {"rag": "false"}
    assert body == "# Title\n\nProse.\n"
    assert "rag" not in body


def test_a_file_without_front_matter_is_returned_untouched():
    text = "# Power Zones\n\nZone 2 is aerobic endurance.\n"

    assert parse_front_matter(text) == ({}, text)


def test_a_comment_explaining_the_exclusion_is_not_read_as_a_key():
    """Prose contains colons; the reason for excluding a file belongs next to it."""
    front, _ = parse_front_matter(
        "---\n# Bookkeeping, not corpus: it cannot answer anything.\nrag: false\n---\nBody\n"
    )

    assert front == {"rag": "false"}


def test_an_unclosed_fence_is_a_horizontal_rule_not_a_header():
    text = "---\nJust a document that opens with a rule.\n"

    assert parse_front_matter(text) == ({}, text)


def test_a_malformed_line_costs_the_metadata_not_the_document():
    front, body = parse_front_matter("---\nrag: false\nnonsense\n---\nBody\n")

    assert front == {"rag": "false"}
    assert body == "Body\n"


# ---------------------------------------------------------------------------
# is_corpus_document
# ---------------------------------------------------------------------------


def test_a_file_that_says_nothing_is_corpus():
    """Adding a knowledge file must need no ceremony; only excluding one does."""
    assert is_corpus_document({}) is True


@pytest.mark.parametrize("value", ["false", "False", "FALSE", "no", "off", "0", " false "])
def test_the_marker_is_read_the_way_a_human_would_write_it(value):
    assert is_corpus_document({"rag": value}) is False


@pytest.mark.parametrize("value", ["true", "yes", ""])
def test_anything_else_leaves_the_file_searchable(value):
    """A file that fumbles the marker should be findable, not silently missing."""
    assert is_corpus_document({"rag": value}) is True


# ---------------------------------------------------------------------------
# The actual files
# ---------------------------------------------------------------------------


def test_the_bibliography_is_excluded():
    front, body = parse_front_matter((KNOWLEDGE_DIR / "sources.md").read_text(encoding="utf-8"))

    assert is_corpus_document(front) is False
    # And the heading survives the split, so the file still reads as itself.
    assert body.startswith("# RAG Knowledge Source List")


def test_every_other_knowledge_file_is_still_corpus():
    """Guards the blast radius: one marker must not quietly empty the corpus."""
    included = [
        path.name
        for path in sorted(KNOWLEDGE_DIR.glob("*.md"))
        if is_corpus_document(parse_front_matter(path.read_text(encoding="utf-8"))[0])
    ]

    assert "sources.md" not in included
    assert len(included) >= 10, f"only {len(included)} knowledge files would be ingested"
