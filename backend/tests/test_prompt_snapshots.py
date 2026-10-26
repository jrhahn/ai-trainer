"""Golden snapshots of the prompts (ai-trainer-ops#259).

Each prompt in ``prompt_fixtures.BUILDERS`` is rendered from fixed inputs and
compared with its file in ``tests/snapshots/prompts/``. A wording change fails
here, once, with the diff to read, instead of in a dozen sentence pins spread
over the suite. After a deliberate change:

    uv run pytest tests/test_prompt_snapshots.py --update-snapshots

and commit the changed ``.txt`` files with the change, so the review sees the
new wording.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.prompt_fixtures import BUILDERS

SNAPSHOTS = Path(__file__).parent / "snapshots" / "prompts"


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_prompt_matches_its_snapshot(name: str, request: pytest.FixtureRequest) -> None:
    text = BUILDERS[name]()
    path = SNAPSHOTS / f"{name}.txt"
    if request.config.getoption("--update-snapshots"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert path.exists(), f"no snapshot for {name}: run with --update-snapshots"
    assert text == path.read_text(encoding="utf-8"), (
        f"the {name} prompt changed; if that is meant, run with --update-snapshots and commit the diff"
    )


def test_every_snapshot_has_a_builder() -> None:
    """A snapshot left behind by a removed builder would be checked by nothing."""
    names = {path.stem for path in SNAPSHOTS.glob("*.txt")}
    assert names == set(BUILDERS)
