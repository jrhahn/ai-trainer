"""Ceilings and expiry for the coach's uncertainty records (#581).

74 hypotheses proposed, none confirmed, none refuted, 96 % never observed a
second time. The inflow was ~18 a week and the outflow was zero. These tests pin
the two things that were missing: a stated capacity, and an end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.uncertainty_lifecycle import (
    EXPERIMENT_POLICY,
    HYPOTHESIS_POLICY,
    MAX_FALSIFIABLE_STATEMENT_CHARS,
    OPEN_QUESTION_POLICY,
    POLICIES,
    age_in_days,
    capacity_for,
    expiry_reason,
    governs_hypothesis_category,
    has_expired,
    is_falsifiable_statement,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


# --- Expiry ----------------------------------------------------------------


def test_a_hypothesis_never_observed_again_does_not_stay_proposed_forever():
    """The acceptance criterion, stated directly."""
    assert has_expired(
        last_seen=_days_ago(HYPOTHESIS_POLICY.unsupported_after_days + 1),
        evidence_count=1,
        policy=HYPOTHESIS_POLICY,
        now=NOW,
    )


def test_a_fresh_hypothesis_is_left_alone():
    assert not has_expired(
        last_seen=_days_ago(3), evidence_count=1, policy=HYPOTHESIS_POLICY, now=NOW
    )


def test_a_hypothesis_that_did_recur_gets_a_longer_grace():
    """These are the only records that ever worked, and they are the ground truth
    a later value gate has to calibrate against — so they are not thrown away on
    the same clock as a one-off."""
    stale_for_a_singleton = _days_ago(HYPOTHESIS_POLICY.unsupported_after_days + 5)
    assert has_expired(
        last_seen=stale_for_a_singleton,
        evidence_count=1,
        policy=HYPOTHESIS_POLICY,
        now=NOW,
    )
    assert not has_expired(
        last_seen=stale_for_a_singleton,
        evidence_count=4,
        policy=HYPOTHESIS_POLICY,
        now=NOW,
    )


def test_even_a_well_supported_record_eventually_goes_quiet_enough():
    assert has_expired(
        last_seen=_days_ago(HYPOTHESIS_POLICY.supported_after_days + 1),
        evidence_count=9,
        policy=HYPOTHESIS_POLICY,
        now=NOW,
    )


def test_the_boundary_is_where_the_policy_says():
    exactly = _days_ago(HYPOTHESIS_POLICY.unsupported_after_days)
    assert has_expired(
        last_seen=exactly, evidence_count=1, policy=HYPOTHESIS_POLICY, now=NOW
    )
    assert not has_expired(
        last_seen=exactly + timedelta(hours=1),
        evidence_count=1,
        policy=HYPOTHESIS_POLICY,
        now=NOW,
    )


def test_a_naive_timestamp_is_read_as_utc():
    """SQLite hands timestamps back without a zone; comparing them raw raises."""
    naive = _days_ago(HYPOTHESIS_POLICY.unsupported_after_days + 1).replace(tzinfo=None)
    assert has_expired(
        last_seen=naive, evidence_count=1, policy=HYPOTHESIS_POLICY, now=NOW
    )
    assert age_in_days(naive, NOW) == HYPOTHESIS_POLICY.unsupported_after_days + 1


# --- The audit trail -------------------------------------------------------


def test_the_reason_says_which_of_the_two_windows_ran_out():
    unsupported = expiry_reason(
        last_seen=_days_ago(40), evidence_count=1, policy=HYPOTHESIS_POLICY, now=NOW
    )
    assert "Never observed a second time in 40 days" in unsupported

    supported = expiry_reason(
        last_seen=_days_ago(200), evidence_count=5, policy=HYPOTHESIS_POLICY, now=NOW
    )
    assert "5 observations" in supported
    assert "nothing new for 200 days" in supported


# --- Capacity --------------------------------------------------------------


def test_capacity_is_what_is_left_under_the_ceiling():
    assert capacity_for(open_count=0, policy=HYPOTHESIS_POLICY) == (
        HYPOTHESIS_POLICY.ceiling
    )
    assert capacity_for(open_count=HYPOTHESIS_POLICY.ceiling - 1, policy=HYPOTHESIS_POLICY) == 1


def test_an_overfull_channel_reports_no_capacity_rather_than_a_negative_one():
    """Prod had 74 open where the ceiling is 12; the arithmetic must not invite
    a caller to ask for -62 candidates."""
    assert capacity_for(open_count=74, policy=HYPOTHESIS_POLICY) == 0


@pytest.mark.parametrize("policy", list(POLICIES.values()))
def test_every_channel_states_a_ceiling_and_a_window(policy):
    """The failure was that three channels had only an entry threshold. Whatever
    the numbers are, each channel has to have both."""
    assert policy.ceiling > 0
    assert policy.unsupported_after_days > 0
    assert policy.supported_after_days >= policy.unsupported_after_days


def test_an_experiment_is_the_tightest_channel():
    """It asks the athlete to *do* something. Twenty outstanding is homework."""
    assert EXPERIMENT_POLICY.ceiling < OPEN_QUESTION_POLICY.ceiling
    assert OPEN_QUESTION_POLICY.ceiling < HYPOTHESIS_POLICY.ceiling


# --- Which hypotheses this owns --------------------------------------------


@pytest.mark.parametrize(
    "category", ["fatigue_response", "general", "coaching_risk", None]
)
def test_the_free_form_hypotheses_are_governed(category):
    assert governs_hypothesis_category(category)


@pytest.mark.parametrize("category", ["performance_model", "weather_preference"])
def test_the_deterministic_writers_are_left_to_their_own_lifecycle(category):
    """They re-derive their claims every pass and already retire what the model
    stops supporting. A ceiling here would make their decay pass retire a
    hypothesis the model still backs."""
    assert not governs_hypothesis_category(category)


# --- Falsifiability --------------------------------------------------------


def test_a_repeatable_claim_passes():
    assert is_falsifiable_statement(
        "Strength training the day before suppresses heart-rate response."
    )


def test_the_compound_claims_from_production_are_rejected():
    """Verbatim from the issue. A configuration this specific will not recur, so
    the claim can never gain a second observation — which is exactly how 96 % of
    hypotheses got stuck at one."""
    assert not is_falsifiable_statement(
        "Performing a yoga session immediately following a consecutive strength "
        "and high-intensity cycling day results in a measurably elevated fatigue "
        "response on the subsequent endurance ride."
    )


def test_an_empty_statement_is_not_falsifiable_either():
    assert not is_falsifiable_statement("")
    assert not is_falsifiable_statement("   ")


def test_the_limit_is_measured_after_whitespace_is_collapsed():
    """A model that pretty-prints its statement must not be judged on its
    indentation."""
    padded = "  Strength   training   the day before\n suppresses  HR response.  "
    assert is_falsifiable_statement(padded)
    assert len(" ".join(padded.split())) <= MAX_FALSIFIABLE_STATEMENT_CHARS
