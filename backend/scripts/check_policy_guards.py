"""Check that the numbers this codebase decides with are load-bearing (#600).

For each entry in :data:`tests.policy_guards.GUARDS`: move the constant, run the
tests that own it, and require them to fail. A constant that survives being moved
a long way is either not policy or not tested.

    uv run python scripts/check_policy_guards.py            # all of them
    uv run python scripts/check_policy_guards.py -k load    # a subset

This is the repeatable version of a check that was being done by hand — change
the number, run the suite, change it back, report the result in the pull request
— which meant the repository knew nothing about it and code could be loosened
together with its test.

Each guard runs only the tests that own its constant, so the whole pass costs
about as much as one suite run rather than one per guard. ``-x`` stops each run
at the first failure, since one is all that is being asked for.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from tests.policy_guards import GUARDS, PolicyGuard  # noqa: E402

# pytest's exit code for "tests ran and at least one of them failed". The other
# non-zero codes (2 interrupted, 3 internal error, 4 usage error, 5 nothing
# collected) all mean the run never measured anything — see `_run` (#674).
TESTS_FAILED = 1

Outcome = Literal["held", "slipped", "error"]

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)


def _run(guard: PolicyGuard) -> tuple[Outcome, str]:
    """How the perturbed run ended. Second value is the detail.

    Only pytest's exit code 1 — tests ran and at least one failed — is evidence
    that something depends on the constant. Every other non-zero exit means the
    run never got as far as measuring: a ``PerturbationError`` for a constant
    that has been renamed is raised in ``pytest_configure`` and surfaces as 3 or
    4, an empty collection as 5. Counting those as held would let the register
    stop guarding exactly when the code moves, which is the failure this script
    exists to catch, one level up (#674).
    """
    missing = [path for path in guard.tests if not (BACKEND / path).exists()]
    if missing:
        return "error", f"test file does not exist: {', '.join(missing)}"

    command = [
        sys.executable,
        "-m",
        "pytest",
        *guard.tests,
        "-x",
        "-q",
        "--no-header",
        "-p",
        "tests.policy_guard_plugin",
        "--perturb",
        guard.spec,
    ]
    completed = subprocess.run(
        command,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        # The suite's conftest re-execs on NixOS unless this is already set,
        # which would swallow the result of the subprocess (see CLAUDE.md).
        env={**os.environ, "_PYTEST_NIXOS_REEXEC": "1"},
    )
    if completed.returncode == TESTS_FAILED:
        return "held", ""

    tail = (completed.stdout or completed.stderr).strip().splitlines()
    detail = tail[-1] if tail else ""
    if completed.returncode != 0:
        suffix = f": {detail}" if detail else ""
        return "error", f"pytest exited {completed.returncode}, not a test failure{suffix}"
    return "slipped", detail or "the tests passed unchanged"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", dest="pattern", default="", help="substring filter")
    args = parser.parse_args()

    selected = [g for g in GUARDS if args.pattern in g.target]
    if not selected:
        print(f"No guard matches {args.pattern!r}", file=sys.stderr)
        return 2

    held: list[PolicyGuard] = []
    unguarded: list[tuple[PolicyGuard, str]] = []
    broken: list[tuple[PolicyGuard, str]] = []
    recorded: list[PolicyGuard] = []

    for guard in selected:
        if guard.moved_to is None:
            recorded.append(guard)
            print(f"{YELLOW}record{RESET} {guard.target}\n       {DIM}{guard.reason}{RESET}")
            continue
        started = time.monotonic()
        outcome, detail = _run(guard)
        took = time.monotonic() - started
        if outcome == "held":
            held.append(guard)
            print(f"{GREEN}held{RESET}    {guard.spec} {DIM}({took:.0f}s){RESET}")
        elif outcome == "slipped":
            unguarded.append((guard, detail))
            print(f"{RED}SLIPPED{RESET} {guard.spec} {DIM}({took:.0f}s){RESET}")
            print(f"        {DIM}{detail}{RESET}")
        else:
            broken.append((guard, detail))
            print(f"{RED}ERROR{RESET}   {guard.spec} {DIM}({took:.0f}s){RESET}")
            print(f"        {DIM}{detail}{RESET}")

    print(
        f"\n{len(held)} held, {len(unguarded)} slipped through, "
        f"{len(broken)} could not run, "
        f"{len(recorded)} recorded as unguarded."
    )
    for guard, detail in unguarded:
        print(
            f"\n{RED}{guard.target}{RESET} decides {guard.decides}, and moving it to "
            f"{guard.moved_to} broke nothing in {', '.join(guard.tests)}.\n"
            f"Either write the test that depends on it, or give the guard a "
            f"`reason` instead of a `moved_to` so the gap is on the record."
        )
    for guard, detail in broken:
        print(
            f"\n{RED}{guard.target}{RESET} decides {guard.decides}, and the run that "
            f"was supposed to measure it never got there — {detail}.\n"
            f"Nothing was measured, so this is not a pass. Check that the target "
            f"still exists under that name and that {', '.join(guard.tests)} still "
            f"collects."
        )
    return 1 if unguarded or broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
