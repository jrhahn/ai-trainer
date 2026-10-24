"""A move to a named day is a move, not an added session (ai-trainer-ops#229).

Mia (Tue, Thu, Sun) asked "This Thursday doesn't work, can we move the session
to Friday?" and was refused: Friday "would make four sessions for the week".
The constraint rule said to choose "the best available day", and nothing said
that a day the athlete names is available that week, or that a move keeps the
count.
"""

from __future__ import annotations

import pytest

from services.prompts import ask_trainer_plan_updates_rule


@pytest.mark.parametrize("context_workout", [None, {"date": "2026-10-29", "title": "Endurance"}])
def test_the_coach_is_told_a_named_day_takes_the_session(context_workout):
    rule = ask_trainer_plan_updates_rule(context_workout)

    assert "makes that day available for that week, even when it is not one of their training days" in rule
    assert "the old day becomes rest and the named day holds the session" in rule
    assert "never an added session" in rule
    assert "Ask only when the named day collides with a constraint the athlete stated" in rule
