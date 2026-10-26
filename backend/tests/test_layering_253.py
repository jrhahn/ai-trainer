"""The backend's layers, and a guard that holds them (ai-trainer-ops#253).

    config → database → models → schemas → crud → services → auth, routers → main

A module imports only from the layers to its left. There was no written rule,
so every new cycle was "fixed" with an import moved into a function, and a test
could not import a schema without pulling in the planner.

``KNOWN`` names the module-level imports that break the rule today, each with
its reason. A new one fails this test, and so does one that is fixed but still
listed: the list only shrinks. The imports inside functions in services/ are
counted too. The goal is ten or fewer, each with a reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LAYER = {"config": 0, "database": 1, "models": 2, "schemas": 3, "crud": 4, "services": 5, "auth": 6, "routers": 6, "main": 7}

_PURE_HELPERS = "a pure helper schemas and crud share with the services; it moves below them in a later stage"
KNOWN: dict[tuple[str, str], str] = {
    ("crud.py", "services.activity_identity"): _PURE_HELPERS,
    ("crud.py", "services.analysis"): "build_rule_based_summary for a stored ride; it moves out of analysis",
    ("crud.py", "services.athlete_states"): _PURE_HELPERS,
    ("crud.py", "services.motivation_model"): _PURE_HELPERS,
    ("crud.py", "services.plan_compliance"): _PURE_HELPERS,
    ("crud.py", "services.ride_purpose_question"): _PURE_HELPERS,
    ("crud.py", "services.strength_model"): _PURE_HELPERS,
    ("crud.py", "services.uncertainty_lifecycle"): _PURE_HELPERS,
    ("crud.py", "services.uncertainty_value"): _PURE_HELPERS,
    ("schemas.py", "services.activity_identity"): _PURE_HELPERS,
    ("schemas.py", "services.day_preference"): _PURE_HELPERS,
    ("schemas.py", "services.fitness_ledger"): _PURE_HELPERS,
    ("schemas.py", "services.ride_purpose_question"): _PURE_HELPERS,
    ("schemas.py", "services.run_model"): _PURE_HELPERS,
    ("schemas.py", "services.untrusted_text"): _PURE_HELPERS,
    ("services/authelia_store.py", "auth"): (
        "the AUTHELIA_* constants and the password helpers; 93 tests patch them on auth, "
        "so they move together in a stage of their own"
    ),
}
# Imports of backend modules inside a function in services/. The goal is ten or
# fewer, each with a one-line reason. Lower this number when you remove one.
LAZY_IMPORTS_IN_SERVICES = 27


def _checked_files() -> list[Path]:
    top = [BACKEND / f"{name}.py" for name in ("config", "database", "models", "schemas", "crud", "auth")]
    return top + sorted((BACKEND / "services").glob("*.py"))


def _layer_of(path: Path) -> int:
    relative = path.relative_to(BACKEND)
    return LAYER["services"] if relative.parts[0] == "services" else LAYER[relative.stem]


def _targets(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.ImportFrom):
        if node.module == "services":
            return [f"services.{alias.name}" for alias in node.names]
        return [node.module or ""]
    return [alias.name for alias in node.names]


def _is_backend(target: str) -> bool:
    return target.split(".")[0] in LAYER


def test_no_module_imports_a_layer_to_its_right_beyond_the_known_ones():
    found = set()
    for path in _checked_files():
        own = _layer_of(path)
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _targets(node):
                    if _is_backend(target) and LAYER[target.split(".")[0]] > own:
                        found.add((str(path.relative_to(BACKEND)), target))

    assert found - KNOWN.keys() == set(), "a new import breaks the layering"
    assert KNOWN.keys() - found == set(), "fixed: remove it from KNOWN"


def test_the_imports_inside_functions_in_services_do_not_grow():
    count = 0
    for path in sorted((BACKEND / "services").glob("*.py")):
        for function in ast.walk(ast.parse(path.read_text())):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                count += sum(
                    1
                    for node in ast.walk(function)
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                    for target in _targets(node)
                    if _is_backend(target)
                )

    assert count == LAZY_IMPORTS_IN_SERVICES, (
        f"{count} imports inside functions in services/, {LAZY_IMPORTS_IN_SERVICES} recorded: "
        "a new one needs the cycle fixed instead, and one removed lowers the number"
    )


def test_every_known_exception_says_why():
    assert all(len(reason) > 20 for reason in KNOWN.values())
