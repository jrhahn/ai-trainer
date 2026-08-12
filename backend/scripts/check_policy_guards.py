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

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from tests.policy_guards import GUARDS, PolicyGuard  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)


def _run(guard: PolicyGuard) -> tuple[bool, str]:
    """True when the perturbation was noticed. Second value is the detail."""
    missing = [path for path in guard.tests if not (BACKEND / path).exists()]
    if missing:
        return False, f"test file does not exist: {', '.join(missing)}"

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
    if completed.returncode != 0:
        return True, ""

    tail = (completed.stdout or completed.stderr).strip().splitlines()
    return False, tail[-1] if tail else "the tests passed unchanged"


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
    recorded: list[PolicyGuard] = []

    for guard in selected:
        if guard.moved_to is None:
            recorded.append(guard)
            print(f"{YELLOW}record{RESET} {guard.target}\n       {DIM}{guard.reason}{RESET}")
            continue
        started = time.monotonic()
        noticed, detail = _run(guard)
        took = time.monotonic() - started
        if noticed:
            held.append(guard)
            print(f"{GREEN}held{RESET}   {guard.spec} {DIM}({took:.0f}s){RESET}")
        else:
            unguarded.append((guard, detail))
            print(f"{RED}SLIPPED{RESET} {guard.spec} {DIM}({took:.0f}s){RESET}")
            print(f"        {DIM}{detail}{RESET}")

    print(
        f"\n{len(held)} held, {len(unguarded)} slipped through, "
        f"{len(recorded)} recorded as unguarded."
    )
    for guard, detail in unguarded:
        print(
            f"\n{RED}{guard.target}{RESET} decides {guard.decides}, and moving it to "
            f"{guard.moved_to} broke nothing in {', '.join(guard.tests)}.\n"
            f"Either write the test that depends on it, or give the guard a "
            f"`reason` instead of a `moved_to` so the gap is on the record."
        )
    return 1 if unguarded else 0


if __name__ == "__main__":
    raise SystemExit(main())
