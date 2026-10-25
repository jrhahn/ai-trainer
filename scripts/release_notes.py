#!/usr/bin/env python3
"""Release notes for one side of the repository, from its merge commits.

    python3 scripts/release_notes.py backend backend-v0.152.28 HEAD

The changelogs stopped being written by hand (ai-trainer-ops#288): every pull
request edited their first lines, so every merge train conflicted there. The
notes are the titles of the pull requests merged into develop in
``<previous>..<new>`` that touched ``<side>/``, one line each with its link. A
merge train is listed by the pull requests it carried, and Dependabot's are
grouped at the end. A title loses its trailing reference to the private tracker.

Standard library and git only, so the release workflow needs nothing installed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

REPO_URL = "https://github.com/jrhahn/ai-trainer"

_PR_MERGE = re.compile(r"^Merge pull request #(\d+) from (\S+)")
# How a merge train takes in a pull request's head unchanged (see the 2026-10-25 train).
_TRAIN_MEMBER = re.compile(r"^Merge #(\d+) \((\S+)\) into the train")
_TRACKER_REF = re.compile(r"\s*\((?:ai-trainer-ops#\d+(?:,\s*)?)+\)\s*$")
_FIELD, _RECORD = "\x1f", "\x1e"


@dataclass(frozen=True)
class Entry:
    number: int
    title: str
    dependency: bool


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, cwd=cwd
    ).stdout


def _merges(rev_range: str, path: str, cwd: str | None) -> list[tuple[str, str, str]]:
    """First-parent merges in the range that changed ``path``, oldest first."""
    out = _git(
        "log", "--first-parent", "--merges", "--reverse",
        f"--format=%H{_FIELD}%s{_FIELD}%b{_RECORD}", rev_range, "--", path,
        cwd=cwd,
    )
    merges = []
    for record in out.split(_RECORD):
        if record.strip():
            sha, subject, body = record.strip("\n").split(_FIELD, 2)
            merges.append((sha, subject, body))
    return merges


def clean_title(title: str) -> str:
    return _TRACKER_REF.sub("", title.strip())


def _is_dependabot(branch: str) -> bool:
    return "dependabot/" in branch


def entries(side: str, rev_range: str, cwd: str | None = None) -> list[Entry]:
    found: list[Entry] = []
    for sha, subject, body in _merges(rev_range, f"{side}/", cwd):
        merge = _PR_MERGE.match(subject)
        if not merge:
            continue
        title = next((line for line in body.splitlines() if line.strip()), merge[2])
        if title.startswith("Merge train"):
            found.extend(_train(sha, side, cwd))
            continue
        found.append(Entry(int(merge[1]), clean_title(title), _is_dependabot(merge[2])))
    return found


def _train(sha: str, side: str, cwd: str | None) -> list[Entry]:
    """The pull requests a merge train carried.

    Git does not know a pull request's title. A train's merge can carry it as
    the first line of its body; otherwise the pull request's first commit
    stands in for it, since its last one is often a review fix.
    """
    base = _git("merge-base", f"{sha}^1", f"{sha}^2", cwd=cwd).strip()
    carried = []
    for member, subject, body in _merges(f"{base}..{sha}^2", f"{side}/", cwd):
        merge = _TRAIN_MEMBER.match(subject)
        if not merge:
            continue
        title = _stated_title(body)
        if not title:
            own = _git(
                "log", "--reverse", "--format=%s", f"{member}^1..{member}^2", cwd=cwd
            ).splitlines()
            title = own[0] if own else merge[2]
        carried.append(Entry(int(merge[1]), clean_title(title), _is_dependabot(merge[2])))
    return carried


def _stated_title(body: str) -> str:
    """The first line of a train merge's body, unless it is what git wrote
    there itself: a resolved merge's "# Conflicts:" list."""
    first = next((line for line in body.splitlines() if line.strip()), "")
    if first.startswith(("#", "Conflicts:")) or first[:1].isspace():
        return ""
    return first


def render(side: str, found: list[Entry]) -> str:
    def line(entry: Entry) -> str:
        return f"- {entry.title} ([#{entry.number}]({REPO_URL}/pull/{entry.number}))"

    changes = [line(e) for e in found if not e.dependency]
    dependencies = [line(e) for e in found if e.dependency]
    if not changes and not dependencies:
        return f"No pull request changed {side}/ since the previous release.\n"
    parts = []
    if changes:
        parts += ["## Changes", "", *changes, ""]
    if dependencies:
        parts += ["## Dependencies", "", *dependencies, ""]
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ("backend", "frontend"):
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    side, previous, new = argv[1:]
    sys.stdout.write(render(side, entries(side, f"{previous}..{new}")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
