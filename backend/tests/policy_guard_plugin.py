"""Run the suite with one policy constant moved (#600).

    pytest tests/test_uncertainty_value.py \
        -p tests.policy_guard_plugin \
        --perturb "services.uncertainty_value:NEUTRAL_RELEVANCE=0.9"

The point is the inverse of a normal test run: the perturbed run is expected to
*fail*. A constant that can be moved a long way without any test noticing is
either not policy or not tested, and both are worth finding out.

Nothing is written to disk. The attribute is set on the imported module before
collection, so a run that is killed halfway cannot leave the working tree
modified — which rules out the obvious alternative of rewriting the source file
and restoring it in a ``finally``.

The mechanism only reaches constants that are read when the code runs. One
folded into a derived value at import time would survive this untouched, and
:mod:`scripts.check_policy_guards` reports that as unguarded rather than
silently passing — which is the correct answer either way: a number nothing can
be observed to depend on is not one this repository can claim to be checking.
"""

from __future__ import annotations

import ast
import importlib
import re

_SPEC = re.compile(
    r"^(?P<module>[\w.]+):(?P<attr>\w+)(?:\[(?P<key>[^\]]+)\])?=(?P<value>.+)$"
)


class PerturbationError(RuntimeError):
    """The spec named something that does not exist."""


def apply_perturbation(spec: str) -> str:
    """Apply one ``module:NAME[key]=value`` spec. Returns a human description."""
    match = _SPEC.match(spec.strip())
    if match is None:
        raise PerturbationError(
            f"Not a perturbation spec: {spec!r}. "
            "Expected module:NAME=value or module:NAME[key]=value."
        )
    module_name = match.group("module")
    attr = match.group("attr")
    key = match.group("key")
    raw = match.group("value")

    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        value = raw

    module = importlib.import_module(module_name)
    if not hasattr(module, attr):
        raise PerturbationError(f"{module_name} has no {attr}")

    if key is None:
        before = getattr(module, attr)
        setattr(module, attr, value)
        return f"{module_name}.{attr}: {before!r} -> {value!r}"

    container = getattr(module, attr)
    try:
        before = container[key]
    except (KeyError, TypeError) as exc:
        raise PerturbationError(f"{module_name}.{attr} has no key {key!r}") from exc
    container[key] = value
    return f"{module_name}.{attr}[{key}]: {before!r} -> {value!r}"


def pytest_addoption(parser):
    parser.addoption(
        "--perturb",
        action="append",
        default=[],
        metavar="MODULE:NAME[=]VALUE",
        help="Move a policy constant before collection (#600). Repeatable.",
    )


def pytest_configure(config):
    for spec in config.getoption("--perturb"):
        print(f"[policy-guard] {apply_perturbation(spec)}")
