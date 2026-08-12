"""The policy-guard registry itself has to stay honest (#600).

The expensive half — moving each constant and requiring the tests to fail — is
`scripts/check_policy_guards.py` and runs as its own CI job, because it costs
about a suite run. These are the cheap checks that belong in the ordinary suite,
so a rename or a deletion is caught in seconds rather than in the slow job.

They also cover the way this could rot silently: a registry that shrinks to
nothing, or an entry pointing at a constant that no longer exists, would both
otherwise read as "everything held".
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tests.policy_guard_plugin import PerturbationError, _SPEC
from tests.policy_guards import GUARDS

BACKEND = Path(__file__).resolve().parent.parent


def test_there_is_something_to_check():
    """A registry that empties must not read as a clean pass."""
    assert len(GUARDS) >= 12


def test_the_guards_are_spread_across_the_things_that_decide():
    """One module's constants being well guarded says nothing about the rest."""
    modules = {guard.target.split(":", 1)[0] for guard in GUARDS}
    assert len(modules) >= 4


@pytest.mark.parametrize("guard", GUARDS, ids=lambda g: g.target)
def test_the_constant_still_exists(guard):
    """The registry names constants by string, so a rename would otherwise turn
    a guard into a silent no-op."""
    match = _SPEC.match(f"{guard.target}=0")
    assert match is not None, f"{guard.target} is not a parseable target"

    module = importlib.import_module(match.group("module"))
    attr = match.group("attr")
    assert hasattr(module, attr), f"{match.group('module')} no longer has {attr}"

    key = match.group("key")
    if key is not None:
        container = getattr(module, attr)
        assert key in container, f"{attr} no longer has the key {key!r}"


@pytest.mark.parametrize("guard", GUARDS, ids=lambda g: g.target)
def test_the_tests_that_own_it_still_exist(guard):
    assert guard.tests, f"{guard.target} names no owning tests"
    for path in guard.tests:
        assert (BACKEND / path).exists(), f"{path} does not exist"


@pytest.mark.parametrize("guard", GUARDS, ids=lambda g: g.target)
def test_a_guard_either_moves_the_number_or_says_why_not(guard):
    """An entry with no perturbation is a recorded gap, and a recorded gap has
    to say what is missing — otherwise it is just a way of passing."""
    if guard.moved_to is None:
        assert guard.reason.strip(), (
            f"{guard.target} is recorded as unguarded without saying what is missing"
        )
    assert guard.decides.strip(), f"{guard.target} does not say what it decides"


@pytest.mark.parametrize("guard", GUARDS, ids=lambda g: g.target)
def test_the_perturbation_is_actually_a_change(guard):
    """Moving a constant to the value it already has would pass forever."""
    if guard.moved_to is None:
        return
    match = _SPEC.match(guard.spec)
    assert match is not None, f"{guard.spec} is not a parseable spec"

    module = importlib.import_module(match.group("module"))
    current = getattr(module, match.group("attr"))
    key = match.group("key")
    if key is not None:
        current = current[key]

    assert str(current) != str(guard.moved_to), (
        f"{guard.target} is 'moved' to the value it already holds"
    )


def test_a_spec_that_names_nothing_is_an_error_rather_than_a_pass():
    """The plugin's own failure mode: a typo'd target must not quietly do
    nothing and leave the run looking like a guard that held."""
    from tests.policy_guard_plugin import apply_perturbation

    with pytest.raises(PerturbationError):
        apply_perturbation("services.training_load:NO_SUCH_CONSTANT=1")
    with pytest.raises(PerturbationError):
        apply_perturbation("services.training_load:DEFAULT_LOAD_PER_HOUR[nope]=1")
    with pytest.raises(PerturbationError):
        apply_perturbation("not a spec at all")


def test_a_perturbation_takes_effect_and_is_undone_by_process_exit():
    """The mechanism, demonstrated on a constant of its own choosing.

    Nothing is written to disk — the attribute is set on the imported module —
    so the blast radius of a killed run is the process, not the working tree.
    """
    from tests.policy_guard_plugin import apply_perturbation
    from services import training_load

    original = training_load.FALLBACK_LOAD_PER_HOUR
    try:
        described = apply_perturbation("services.training_load:FALLBACK_LOAD_PER_HOUR=1.0")
        assert training_load.FALLBACK_LOAD_PER_HOUR == 1.0
        assert "FALLBACK_LOAD_PER_HOUR" in described and "1.0" in described
    finally:
        training_load.FALLBACK_LOAD_PER_HOUR = original
