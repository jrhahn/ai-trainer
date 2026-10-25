"""The release notes come from the merged pull requests, and the changelogs stay
frozen (ai-trainer-ops#288).

Every pull request edited the first lines of both changelogs, so every merge
train conflicted there, and the version headers had stopped seven weeks before.
``scripts/release_notes.py`` writes the notes per side from the merge commits;
these tests run it against a small repository built the way this one is merged.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_notes", REPO / "scripts" / "release_notes.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up by name while it is being defined.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release_notes = _module()


class _Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.git("init", "-q", "-b", "develop")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            cwd=self.path, check=True, capture_output=True, text=True,
        ).stdout

    def commit(self, path: str, subject: str) -> None:
        file = self.path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(file.read_text() + subject + "\n" if file.exists() else subject + "\n")
        self.git("add", path)
        self.git("commit", "-q", "-m", subject)

    def pull_request(self, number: int, branch: str, title: str, commits: list[tuple[str, str]], into: str = "develop") -> None:
        self.git("switch", "-q", "-c", branch, "develop")
        for path, subject in commits:
            self.commit(path, subject)
        self.git("switch", "-q", into)
        if into == "develop":
            self.git("merge", "-q", "--no-ff", branch, "-m", f"Merge pull request #{number} from jrhahn/{branch}", "-m", title)
        else:
            self.git("merge", "-q", "--no-ff", branch, "-m", f"Merge #{number} ({branch}) into the train")


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> _Repo:
    """A backend PR, a frontend PR, a Dependabot PR and a merge train of two."""
    repo = _Repo(tmp_path_factory.mktemp("release-notes"))
    repo.commit("backend/app.py", "Start the backend")
    repo.commit("frontend/app.ts", "Start the frontend")
    repo.git("tag", "backend-v0.1.0")
    repo.git("tag", "frontend-v0.1.0")
    repo.pull_request(11, "fix/sync", "Sync every athlete (ai-trainer-ops#5)", [("backend/app.py", "Sync every athlete")])
    repo.pull_request(12, "fix/button", "Make the button larger (ai-trainer-ops#6)", [("frontend/app.ts", "Make the button larger")])
    repo.pull_request(
        13, "dependabot/uv/backend/httpx", "Bump httpx from 0.27 to 0.28 in /backend",
        [("backend/uv.lock", "Bump httpx")],
    )
    repo.git("switch", "-q", "-c", "train/today", "develop")
    repo.pull_request(
        14, "fix/import", "",
        [("backend/app.py", "Keep the import across a restart (ai-trainer-ops#7)"), ("backend/app.py", "Word the log line (review)")],
        into="train/today",
    )
    repo.pull_request(15, "fix/chart", "", [("frontend/app.ts", "Draw the chart on a phone")], into="train/today")
    repo.git("switch", "-q", "develop")
    repo.git("merge", "-q", "--no-ff", "train/today", "-m", "Merge pull request #16 from jrhahn/train/today", "-m", "Merge train of today: #14 and #15")
    return repo


def test_the_backend_notes_list_the_pull_requests_that_changed_the_backend(repo: _Repo):
    found = release_notes.entries("backend", "backend-v0.1.0..develop", cwd=str(repo.path))

    assert [(e.number, e.title, e.dependency) for e in found] == [
        (11, "Sync every athlete", False),
        (13, "Bump httpx from 0.27 to 0.28 in /backend", True),
        # The train is listed by what it carried, under the pull request's own
        # first commit rather than its last, a review fix.
        (14, "Keep the import across a restart", False),
    ]


def test_the_frontend_notes_leave_the_backend_out(repo: _Repo):
    found = release_notes.entries("frontend", "frontend-v0.1.0..develop", cwd=str(repo.path))

    assert [(e.number, e.title) for e in found] == [(12, "Make the button larger"), (15, "Draw the chart on a phone")]


def test_the_notes_link_each_pull_request_and_group_the_dependencies(repo: _Repo):
    text = release_notes.render("backend", release_notes.entries("backend", "backend-v0.1.0..develop", cwd=str(repo.path)))

    assert text.splitlines() == [
        "## Changes",
        "",
        "- Sync every athlete ([#11](https://github.com/jrhahn/ai-trainer/pull/11))",
        "- Keep the import across a restart ([#14](https://github.com/jrhahn/ai-trainer/pull/14))",
        "",
        "## Dependencies",
        "",
        "- Bump httpx from 0.27 to 0.28 in /backend ([#13](https://github.com/jrhahn/ai-trainer/pull/13))",
    ]


def test_a_release_with_nothing_merged_says_so(repo: _Repo):
    assert release_notes.render("backend", []) == "No pull request changed backend/ since the previous release.\n"


@pytest.mark.parametrize(
    ("title", "clean"),
    [
        ("Keep the import (ai-trainer-ops#284)", "Keep the import"),
        ("Fetch the summary (ai-trainer-ops#183, ai-trainer-ops#273)", "Fetch the summary"),
        ("Drop the blank line (review of #1066)", "Drop the blank line (review of #1066)"),
        ("Count days by the calendar in two tests", "Count days by the calendar in two tests"),
    ],
)
def test_a_title_loses_only_its_trailing_tracker_reference(title: str, clean: str):
    assert release_notes.clean_title(title) == clean


@pytest.mark.parametrize("side", ["backend", "frontend"])
def test_the_changelogs_stay_frozen(side: str):
    """Nobody adds entries out of habit: the notes are generated now."""
    assert "## [Unreleased]" not in (REPO / side / "CHANGELOG.md").read_text()
