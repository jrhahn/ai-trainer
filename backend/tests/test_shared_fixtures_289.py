"""The ``db`` and ``user`` fixtures live in conftest.py, once (ai-trainer-ops#289).

Fourteen test modules had their own copy of ``db`` and seven their own
``user``, the same few lines each time. A copy drifts: a change to how a test
session ends would have had to be made in every one. A module needs no copy,
since conftest's fixtures reach every test; a fixture that does something else
gets a name of its own.

The same goes for the ``_create_user`` helpers, twenty copies in twelve shapes:
the shared ones are in tests/factories.py, and the copies left can only go.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SHARED = {"db", "user"}

# Modules that still define their own copy. Empty since the PRs that edited the
# last five merged (ai-trainer-ops#289); a new entry needs a reason.
STILL_OWN: dict[str, str] = {}


# Copies of a ``_create_user`` helper still in a test module. Lower it when one
# moves to tests/factories.py.
CREATE_USER_COPIES = 11


def _own_fixtures(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in SHARED
        and any(
            "fixture" in ast.unparse(decorator) for decorator in node.decorator_list
        )
    }


def test_no_test_module_defines_its_own_db_or_user():
    owners = {path.name for path in TESTS.glob("test_*.py") if _own_fixtures(path)}

    assert owners - STILL_OWN.keys() == set(), (
        "use conftest's db and user, or give a different fixture its own name"
    )
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


def test_the_copies_of_create_user_do_not_grow():
    copies = [
        path.name
        for path in sorted(TESTS.glob("test_*.py"))
        for node in ast.parse(path.read_text()).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_create_user"
    ]

    assert len(copies) == CREATE_USER_COPIES, (
        f"{len(copies)} copies of _create_user ({', '.join(copies)}), {CREATE_USER_COPIES} recorded: "
        "use tests/factories.py instead of a new one, and lower the number when one goes"
    )
