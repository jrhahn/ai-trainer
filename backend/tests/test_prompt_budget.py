"""A size budget for the prompts' fixed text (ai-trainer-ops#259).

Nothing failed when a prompt grew by a tenth: the snapshot moves with every
deliberate change, and ``test_prompt_size_bound.py`` bounds the athlete's text,
not ours. Here each prompt's fixed text may grow by up to 10 % over the size
recorded in ``tests/snapshots/prompts/budget.json``. More than that needs a new
record, made on purpose and visible in the review:

    uv run pytest tests/test_prompt_budget.py --update-prompt-budget
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.prompt_fixtures import BUILDERS, FIXED_TEXT

BUDGET = Path(__file__).parent / "snapshots" / "prompts" / "budget.json"
HEADROOM = 1.10


def _recorded() -> dict[str, int]:
    return json.loads(BUDGET.read_text(encoding="utf-8")) if BUDGET.exists() else {}


def test_every_prompt_has_a_recorded_budget(request: pytest.FixtureRequest) -> None:
    if request.config.getoption("--update-prompt-budget"):
        sizes = {name: len(BUILDERS[name]()) for name in FIXED_TEXT}
        BUDGET.parent.mkdir(parents=True, exist_ok=True)
        BUDGET.write_text(
            json.dumps(sizes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert sorted(_recorded()) == FIXED_TEXT


@pytest.mark.parametrize("name", FIXED_TEXT)
def test_fixed_text_stays_within_its_budget(name: str) -> None:
    recorded = _recorded().get(name)
    assert recorded, f"no budget for {name}: run with --update-prompt-budget"
    size = len(BUILDERS[name]())
    assert size <= recorded * HEADROOM, (
        f"the {name} prompt's fixed text is {size} characters, more than 10 % over its "
        f"budget of {recorded}; trim it, or record a new budget with --update-prompt-budget"
    )
