"""Which files in ``backend/knowledge/`` are corpus and which are bookkeeping (#630).

``ingest_seed_corpus`` globs ``*.md``, which swept up ``sources.md`` — the
bibliography listing every paper cited across the other files. It is five chunks
of ``knowledge_chunks``, it was retrieved at 0.712 answering a question about
VO₂max intervals, and it arrived in the coach's prompt under ``[Source: Sources]``
as cycling-science research. A citation list cannot answer anything; it just
spends one of the five slots retrieval has.

Worse since #627: a bibliography honestly repeats "critical power", "lactate
threshold" and "VO₂max" many times over, so it cleared the dominance rule and
earned all three topic tags. Limiter-aware ranking then promoted it ahead of real
prose for every limiter — the most useless document in the corpus became the most
promotable one.

The rule lives in the document rather than in a skip list in the script, so that
someone opening the file can see why it is not corpus, and so the next such file
carries its own answer.
"""

from __future__ import annotations

_FENCE = "---"

# Front-matter key marking a file as documentation *about* the corpus rather than
# part of it. Absent means "this is corpus", so adding a knowledge file needs no
# ceremony — only excluding one does.
_CORPUS_KEY = "rag"
_FALSE_VALUES = frozenset({"false", "no", "off", "0"})


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split leading ``---`` front matter off *text*.

    Returns ``(front_matter, body)``; the body is what gets chunked and embedded,
    so the marker never becomes corpus itself. A file without front matter comes
    back as ``({}, text)`` unchanged.

    Deliberately not YAML: one flat ``key: value`` block plus ``#`` comments is
    the whole format, and a real parser here would invite structure that the
    ingest has no use for. A line without a colon is skipped rather than raising
    — a malformed header should cost the metadata, not the document.
    """
    if not text.startswith(_FENCE):
        return {}, text
    lines = text.split("\n")
    try:
        end = lines.index(_FENCE, 1)
    except ValueError:
        # No closing fence: the file just happens to open with a rule.
        return {}, text

    front: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        # A comment explaining *why* a file is excluded belongs next to the
        # marker, and it will contain prose colons.
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition(":")
        if sep:
            front[key.strip().lower()] = value.strip()
    return front, "\n".join(lines[end + 1 :]).lstrip("\n")


def is_corpus_document(front_matter: dict[str, str]) -> bool:
    """Whether a knowledge file should be ingested and retrievable.

    Defaults to True: the corpus is the normal case, and a file that forgets to
    declare itself should still be searchable rather than silently missing.
    """
    return front_matter.get(_CORPUS_KEY, "").strip().lower() not in _FALSE_VALUES
