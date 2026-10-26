"""The backend's layers, and a guard that holds them (ai-trainer-ops#253).

    config → database → models → schemas → crud → services → auth, routers → main

A module imports only from the layers to its left. There was no written rule,
so every new cycle was "fixed" with an import moved into a function, and a test
could not import a schema without pulling in the planner.

``KNOWN`` names the module-level imports that break the rule today, each with
its reason. Module level is whatever runs when the module loads: the top level,
the blocks there (``if TYPE_CHECKING:``, ``try:``) and class bodies, not a
function's body. A new one fails this test, and so does one that is fixed but
still listed: the list only shrinks. The imports inside functions in services/
are in ``LAZY`` by file and target, each with the reason it cannot move to the
top, and counted, each once.
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
# Imports of backend modules inside a function in services/, by file and target,
# each with the reason it cannot move to the top: a cycle it breaks, or an extra
# the module must boot without. A new one needs its cycle fixed instead.
LAZY: dict[tuple[str, str], str] = {
    ("services/day_preference.py", "services.plan_priors"): (
        "plan_priors imports schemas, and schemas imports day_preference"
    ),
    ("services/prompts.py", "services.freshness_allocation"): "freshness_allocation imports prompts when it loads",
    ("services/ride_match_pipeline.py", "services.ride_matching"): (
        "ride_matching imports plan_pipeline, which imports this module to register its node"
    ),
    ("services/roi_recommendation.py", "services.training_utility"): (
        "training_utility reads this module's constants when it loads"
    ),
    ("services/scheduler.py", "services.metrics"): "the metrics extra may be missing, and the scheduler boots without it",
    ("services/token_accounting.py", "services.llm"): "llm imports token_accounting when it loads",
}
# How many: a file can import its target in more than one function. Lower it
# when you remove one.
LAZY_IMPORTS_IN_SERVICES = 9


def _service_files(backend: Path = BACKEND) -> list[Path]:
    """services/ and every package under it."""
    return sorted((backend / "services").rglob("*.py"))


def _checked_files(backend: Path = BACKEND) -> list[Path]:
    top = [backend / f"{name}.py" for name in ("config", "database", "models", "schemas", "crud", "auth")]
    return top + _service_files(backend)


def _layer_of(path: Path) -> int:
    relative = path.relative_to(BACKEND)
    return LAYER["services"] if relative.parts[0] == "services" else LAYER[relative.stem]


def _targets(node: ast.Import | ast.ImportFrom, package: str = "") -> list[str]:
    """The backend modules an import names. A relative one is resolved against
    ``package``, the importing module's package, one level up per extra dot:
    ``from .analysis import x`` in services/ is ``services.analysis``, and
    ``from ..models import x`` is ``models``."""
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level:
            parts = package.split(".") if package else []
            base = parts[: max(len(parts) - (node.level - 1), 0)]
            module = ".".join(part for part in (*base, module) if part)
        if node.module is None or module == "services":
            return [".".join(part for part in (module, alias.name) if part) for alias in node.names]
        return [module]
    return [alias.name for alias in node.names]


def _package_of(path: Path) -> str:
    return ".".join(path.relative_to(BACKEND).parts[:-1])


def _is_backend(target: str) -> bool:
    return target.split(".")[0] in LAYER


def _module_level_imports(tree: ast.Module):
    """The imports a module makes when it loads: its top level, the blocks there
    (``if TYPE_CHECKING:``, ``try:``, ``with``), which a cycle would otherwise
    hide in, and class bodies, which run at load too. Not a function's body."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))


def _imports_inside_functions(tree: ast.Module):
    """Each import inside a function once, however deep it is nested."""
    at_load = {id(node) for node in _module_level_imports(tree)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in at_load:
            yield node


def test_no_module_imports_a_layer_to_its_right_beyond_the_known_ones():
    found = set()
    for path in _checked_files():
        own = _layer_of(path)
        for node in _module_level_imports(ast.parse(path.read_text())):
            for target in _targets(node, _package_of(path)):
                if _is_backend(target) and LAYER[target.split(".")[0]] > own:
                    found.add((str(path.relative_to(BACKEND)), target))

    assert found - KNOWN.keys() == set(), "a new import breaks the layering"
    assert KNOWN.keys() - found == set(), "fixed: remove it from KNOWN"


def _lazy_imports() -> list[tuple[str, str]]:
    return [
        (str(path.relative_to(BACKEND)), target)
        for path in _service_files()
        for node in _imports_inside_functions(ast.parse(path.read_text()))
        for target in _targets(node, _package_of(path))
        if _is_backend(target)
    ]


def test_the_imports_inside_functions_in_services_do_not_grow():
    count = len(_lazy_imports())

    assert count == LAZY_IMPORTS_IN_SERVICES, (
        f"{count} imports inside functions in services/, {LAZY_IMPORTS_IN_SERVICES} recorded: "
        "a new one needs the cycle fixed instead, and one removed lowers the number"
    )


def test_every_import_inside_a_function_says_why():
    found = set(_lazy_imports())

    assert found - LAZY.keys() == set(), "a new import inside a function: fix the cycle, or say why in LAZY"
    assert LAZY.keys() - found == set(), "moved to the top: remove it from LAZY"


def test_every_known_exception_says_why():
    assert all(len(reason) > 20 for reason in (*KNOWN.values(), *LAZY.values()))


def test_the_counts_see_each_import_once_and_every_block():
    """Review of #1131: a nested function's import was counted for every function
    around it, and module-level blocks were not read at all."""
    tree = ast.parse(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import routers.ai\n"
        "try:\n"
        "    import crud\n"
        "except ImportError:\n"
        "    pass\n"
        "def outer():\n"
        "    def inner():\n"
        "        import schemas\n"
        "class Holder:\n"
        "    import models\n"
    )

    assert sorted(t for n in _module_level_imports(tree) for t in _targets(n)) == [
        "crud", "models", "routers.ai", "typing"
    ]
    assert sorted(t for n in _imports_inside_functions(tree) for t in _targets(n)) == ["schemas"]


def test_a_relative_import_is_read_as_the_module_it_names():
    """services/ imports relatively in places (review of #1131): ``from .analysis
    import x`` names services.analysis, and ``from . import llm`` services.llm."""
    tree = ast.parse("from .analysis import compute\nfrom . import llm, prompts\n")
    named = [t for node in _module_level_imports(tree) for t in _targets(node, "services")]

    assert sorted(named) == ["services.analysis", "services.llm", "services.prompts"]


def test_each_extra_dot_of_a_relative_import_goes_one_package_up():
    """Review of #1131: ``from ..models import x`` in services/ names models, an
    upward import the guard must see, not services.models."""
    tree = ast.parse("from ..models import User\nfrom .. import crud\n")

    def named(package):
        return sorted(t for n in _module_level_imports(tree) for t in _targets(n, package))

    assert named("services") == ["crud", "models"]
    assert named("services.sub") == ["services.crud", "services.models"]


def test_a_package_under_services_is_checked_too(tmp_path):
    """Review of #1131: a package under services/ would have been skipped."""
    nested = tmp_path / "services" / "planning" / "week.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("")

    assert nested in _checked_files(tmp_path)
    assert nested in _service_files(tmp_path)
