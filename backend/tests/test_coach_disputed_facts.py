"""The coach answers "why did my session change?" from the record (#652).

On 2026-09-05 the nightly job rewrote the athlete's Saturday long ride at 02:00
and recorded exactly why. The coach chat was never shown that record, so when
the athlete asked, it rationalised the end state instead — and when pushed, it
agreed twice and rewrote the plan on the strength of the disagreement alone.

These cover both halves: the record reaches the prompt, and the coach is told
what to do with it.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

from services import prompts

TODAY = datetime.date(2026, 9, 5)  # the Saturday


_KEEP = object()  # distinguishes "use the default day" from an explicit None

_LONG_RIDE = {
    "durationMinutes": 180,
    "workoutType": "endurance",
    "title": "Rainy Weekend Endurance Ride",
}
_RECOVERY_SPIN = {
    "durationMinutes": 45,
    "workoutType": "recovery",
    "title": "Short Local Recovery Trail Spin",
}


def _change(
    date: str = "2026-09-05",
    *,
    source: str = "nightly_maintenance",
    applied: bool = True,
    old=_KEEP,
    new=_KEEP,
    reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        date=date,
        source=source,
        applied=applied,
        recorded_at=datetime.datetime(2026, 9, 5, 0, 0, 14),
        old_day=dict(_LONG_RIDE) if old is _KEEP else old,
        new_day=dict(_RECOVERY_SPIN) if new is _KEEP else new,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def test_the_overnight_rewrite_reaches_the_coach_with_its_reason():
    section = prompts.plan_change_history_section(
        [_change(reason="Shortened the planned long ride to manage overall fatigue.")],
        TODAY,
    )

    assert "2026-09-05 (Saturday)" in section
    assert '180 min endurance "Rainy Weekend Endurance Ride"' in section
    assert '45 min recovery "Short Local Recovery Trail Spin"' in section
    assert "changed by the nightly maintenance run" in section
    assert "Shortened the planned long ride to manage overall fatigue." in section


def test_a_blocked_proposal_is_not_presented_as_a_change():
    """It never reached the athlete's plan, so calling it a change would mislead."""
    section = prompts.plan_change_history_section([_change(applied=False)], TODAY)

    assert section == ""


def test_a_reworded_description_is_not_worth_a_line():
    """Automated runs retitle prose nightly; those rows would bury the real ones."""
    same = {
        "durationMinutes": 75,
        "workoutType": "intervals",
        "title": "Sub-Threshold Climbing Intervals",
    }
    section = prompts.plan_change_history_section(
        [_change(old=dict(same, description="A"), new=dict(same, description="B"))],
        TODAY,
    )

    assert section == ""


def test_a_change_to_a_distant_day_is_left_out():
    section = prompts.plan_change_history_section(
        [_change(date="2026-11-20"), _change(date="2026-01-04")], TODAY
    )

    assert section == ""


def test_a_session_appearing_or_vanishing_reads_as_such():
    added = prompts.plan_change_history_section([_change(old=None)], TODAY)
    removed = prompts.plan_change_history_section([_change(new=None)], TODAY)

    assert "nothing scheduled ->" in added
    assert "-> nothing scheduled" in removed


def test_no_history_costs_no_tokens():
    assert prompts.plan_change_history_section([], TODAY) == ""
    assert prompts.plan_change_history_section(None, TODAY) == ""


def test_a_malformed_date_is_dropped_rather_than_raising():
    assert prompts.plan_change_history_section([_change(date="not-a-date")], TODAY) == ""


def test_the_section_is_placed_with_the_plan_it_explains():
    """The plan is the state; this is how it got there. They are read together."""
    section = prompts.plan_change_history_section([_change(reason="Fatigue.")], TODAY)
    sections = prompts.ask_trainer_system_sections(
        {"name": "Jonas"},
        TODAY.isoformat(),
        [],
        [],
        "",
        "",
        "",
        "",
        plan_changes_section=section,
    )

    assert "Recent plan changes" in sections["plan-changes"]
    names = list(sections)
    assert names.index("plan-changes") == names.index("plan-upcoming") + 1


# ---------------------------------------------------------------------------
# What the coach is told to do with it
# ---------------------------------------------------------------------------


def test_the_coach_separates_a_disputed_fact_from_a_disputed_judgement():
    rule = prompts.disputed_fact_rule()

    assert "FACT" in rule and "JUDGEMENT" in rule
    assert "Do not open with agreement you have not verified." in rule


def test_disagreement_alone_may_not_rewrite_the_plan():
    """The last turn of the #652 transcript: two words of protest, a new plan."""
    rule = prompts.disputed_fact_rule()

    assert "Disagreement alone is not a reason to change the plan" in rule
    assert "never merely to end a disagreement" in rule


def test_the_coach_may_not_dress_up_the_athletes_own_account_as_data():
    rule = prompts.disputed_fact_rule()

    assert "Never state something as retrieved fact" in rule
    assert "Never claim a past plan day was trained because it was scheduled" in rule


def test_the_rule_is_always_present():
    """It qualifies the deferral rules, so it cannot be an optional section."""
    prefix = prompts.coach_static_prefix()

    assert prompts.disputed_fact_rule() in prefix
    assert prefix.index(prompts.disputed_fact_rule()) > prefix.index(
        prompts.reveal_uncertainty_rule()
    )
