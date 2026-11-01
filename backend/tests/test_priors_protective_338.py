"""The prompt names the race and state priors as protective, as the reply
check treats them (review of 2026-11-01, ai-trainer-ops#338 C6)."""

from __future__ import annotations

from datetime import date

from services import plan_priors
from services.plan_priors import PriorDeparture, plan_priors_section

DAY = (date(2026, 11, 3),)
ILL = PriorDeparture(
    plan_priors.PRIOR_ILLNESS, "expert consensus", "Intensity while ill.", DAY
)
RACE_EVE = PriorDeparture(
    plan_priors.PRIOR_RACE_EVE, "expert consensus", "A long ride on the eve.", DAY
)
LONG = PriorDeparture(
    plan_priors.PRIOR_RIDE_LENGTH, "heuristic", "A ride well beyond the longest.", DAY
)


def test_a_state_or_race_departure_is_a_change_to_make_not_to_weigh():
    section = plan_priors_section([ILL, RACE_EVE])

    assert section.startswith("Protective planning priors")
    assert "Intensity while ill." in section and "A long ride on the eve." in section
    assert "Keep such a session only for a reason the athlete gave you" in section
    assert "Use your judgement" not in section


def test_the_other_priors_keep_the_freedom_to_stay():
    section = plan_priors_section([LONG])

    assert section.startswith("Planning priors (computed from the plan")
    assert "Use your judgement" in section
    assert "Protective" not in section


def test_both_kinds_are_listed_each_under_its_own_instruction():
    lines = plan_priors_section([LONG, ILL]).splitlines()

    protective = lines.index("  - [expert consensus] Intensity while ill.")
    judged = lines.index("  - [heuristic] A ride well beyond the longest.")
    assert protective < judged
    assert lines[protective + 1].startswith("Bring the plan back to these.")
    assert lines[judged + 1].startswith("Use your judgement.")


def test_every_standing_prior_is_named_protective():
    """The same set the reply check sends back for (``standing_departures``)."""
    for prior in plan_priors.STANDING_PRIORS:
        departure = PriorDeparture(prior, "expert consensus", "x", DAY)
        assert plan_priors_section([departure]).startswith("Protective")
