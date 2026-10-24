"""The plan writers put sessions only on the training days (ai-trainer-ops#235).

Tom (Mon, Wed, Fri) got weekend rides from the model, as the first scheduling
rule asked ("long endurance and base rides on Saturday and Sunday"). The
training-day guard (#189) turned them into rest, and Friday's "sets up your
legs for the weekend block" and Monday's "recover from a big weekend of base
riding" kept pointing at rides that no longer existed.
"""

from __future__ import annotations

import pytest

from services import prompts


@pytest.mark.parametrize("system", [prompts.generate_plan_system, prompts.adapt_plan_system])
def test_the_plan_writers_are_told_the_training_days_are_the_only_days(system):
    text = system()

    assert "Saturday and Sunday unless" not in text
    assert "the long endurance and base rides on the weekend days among 'trainingDays'" in text
    assert "sessions go only on those weekdays and every other day is rest" in text
    assert "Depart from the time and the sports only" in text
