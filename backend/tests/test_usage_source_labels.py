"""Every usage source is ``<kind>:<kebab-name>`` (#549).

The labels drifted apart once already: endpoints reported ``api:ask_trainer``,
the sync reported ``job:strava-sync``, and nine generator steps reported bare
names like ``coach-narration``. The Grafana panel description claimed the
prefix told you where the spend came from, which was true for two thirds of
the sources.

This walks the real call sites rather than testing the validator against
made-up strings — a validator nothing violates is worth nothing, and the
failure mode here is a *new* call site being written in the old shape.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from services.token_accounting import SOURCE_KINDS, source_is_well_formed

BACKEND = pathlib.Path(__file__).resolve().parent.parent
SCOPE_OPENERS = {
    "track_llm_usage",
    "track_llm_usage_detached",
    "_token_usage_scope",
    "begin_collection",
}


def _source_literals() -> list[tuple[str, int, str]]:
    """Every literal ``source=`` passed to something that opens a scope."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(BACKEND.glob("**/*.py")):
        relative = path.relative_to(BACKEND)
        if relative.parts[0] in {"tests", ".venv", "alembic"}:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name not in SCOPE_OPENERS:
                continue
            for keyword in node.keywords:
                if keyword.arg != "source":
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(
                    keyword.value.value, str
                ):
                    found.append((str(relative), node.lineno, keyword.value.value))
    return found


def test_the_scan_finds_the_call_sites_it_is_meant_to_guard():
    """Guards the guard: a broken scan would pass silently forever."""
    literals = _source_literals()

    assert len(literals) >= 20
    files = {path for path, _line, _source in literals}
    assert any(path.endswith("routers/ai.py") for path in files)
    assert any(path.endswith("services/coach_summary.py") for path in files)


@pytest.mark.parametrize("path,line,source", _source_literals())
def test_every_source_follows_the_convention(path: str, line: int, source: str):
    assert source_is_well_formed(source), (
        f"{path}:{line} uses source={source!r}; expected "
        f"<kind>:<kebab-name> with kind in {', '.join(SOURCE_KINDS)}"
    )


def test_a_source_built_at_runtime_would_still_be_caught():
    """The scan only sees literals, so the runtime check is the real net.

    ``begin_collection`` warns on a malformed source however it was built,
    which covers the f-string case this test cannot see.
    """
    assert not source_is_well_formed("coach-narration")
    assert not source_is_well_formed("api:ask_trainer")
    assert not source_is_well_formed("API:ask-trainer")
    assert not source_is_well_formed("step:")
    assert source_is_well_formed("step:coach-narration")
    assert source_is_well_formed("job:strava-sync")
