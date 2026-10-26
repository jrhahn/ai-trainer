"""The ``db`` and ``user`` fixtures live in conftest.py, once (ai-trainer-ops#289).

Fourteen test modules had their own copy of ``db`` and seven their own
``user``, the same few lines each time. A copy drifts: a change to how a test
session ends would have had to be made in every one. A module needs no copy,
since conftest's fixtures reach every test; a fixture that does something else
gets a name of its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SHARED = {"db", "user"}

# Modules that still define their own copy, because an open PR edits them; the
# list only shrinks. #1122 splits test_crud.py into the three after it, each with
# a copy, so they are named before they exist: a name whose file is not there
# yet is not stale.
_AFTER_1122 = "split out of test_crud.py by an open PR (#1122); its db goes once that has merged"
STILL_OWN = {
    "test_crud.py": _AFTER_1122,
    "test_crud_athlete_memory.py": _AFTER_1122,
    "test_crud_athlete_model.py": _AFTER_1122,
    "test_crud_ride_metrics.py": _AFTER_1122,
    "test_running_pace_model.py": "edited by an open PR (#1121); its db goes once that has merged",
}


def _own_fixtures(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in SHARED
        and any("fixture" in ast.unparse(decorator) for decorator in node.decorator_list)
    }


def test_no_test_module_defines_its_own_db_or_user():
    owners = {path.name for path in TESTS.glob("test_*.py") if _own_fixtures(path)}

    assert owners - STILL_OWN.keys() == set(), "use conftest's db and user, or give a different fixture its own name"
    present = {name for name in STILL_OWN if (TESTS / name).exists()}
    assert present - owners == set(), "its copy is gone: remove it from STILL_OWN"


def test_the_guard_sees_a_fixture_by_its_decorator(tmp_path):
    module = tmp_path / "test_example.py"
    module.write_text(
        "import pytest_asyncio\n"
        "@pytest_asyncio.fixture\n"
        "async def db():\n"
        "    yield None\n"
        "def user():\n"
        "    return None\n"
    )

    # A plain helper called user is not a fixture.
    assert _own_fixtures(module) == {"db"}
