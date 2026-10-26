"""Every backend script is listed in scripts/README.md with where it runs (ai-trainer-ops#290).

A probe from August and two one-off repairs from July sat in scripts/ for months
with nothing running them. The list makes adding a script name its runner, and
makes a deleted one leave the list.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
LISTED = re.compile(r"^\| `([^`]+\.py)` \|", re.M)


def _scripts() -> set[str]:
    return {
        p.relative_to(SCRIPTS).as_posix()
        for p in SCRIPTS.rglob("*.py")
        if p.name != "__init__.py"
    }


def _listed() -> list[str]:
    return LISTED.findall((SCRIPTS / "README.md").read_text())


def test_every_script_is_listed_with_its_runner():
    assert sorted(_scripts() - set(_listed())) == []


def test_every_listed_script_exists():
    assert sorted(set(_listed()) - _scripts()) == []


def test_each_script_is_listed_once():
    listed = _listed()
    assert sorted({name for name in listed if listed.count(name) > 1}) == []
