"""Is this one session recorded in parts, or several separate trainings? (#543)

The distinction decides whether adding durations together means anything. It is
not the same question as duplicate detection, which the rest of
``services.activity_identity`` answers: duplicates describe *the same* ride and
overlap almost entirely, parts of a split session follow one another.
"""

from __future__ import annotations

import pytest

from services.activity_identity import (
    SPLIT_SESSION_MAX_GAP_SECONDS,
    activities_form_one_session,
    non_cycling_classification,
)

DATE = "2026-08-13"


def _activity(start: str, minutes: int, sport_type: str = "Ride"):
    return (sport_type, f"{DATE}T{start}:00Z", minutes * 60)


def test_a_ride_interrupted_by_a_stop_is_one_session():
    """Ended at 10:00, restarted at 10:15 — a café stop, not a second training."""
    assert activities_form_one_session(
        [_activity("09:00", 60), _activity("10:15", 60)]
    )


def test_a_commute_pair_is_not_one_session():
    """The case this exists for: same sport, same day, nine hours apart."""
    assert not activities_form_one_session(
        [_activity("07:30", 60), _activity("17:30", 70)]
    )


def test_different_sports_are_never_one_session():
    assert not activities_form_one_session(
        [_activity("09:00", 45, "WeightTraining"), _activity("10:00", 60, "Ride")]
    )


def test_a_single_activity_is_not_a_split_session():
    """Nothing to add together, so the answer is no by construction."""
    assert not activities_form_one_session([_activity("09:00", 60)])
    assert not activities_form_one_session([])


def test_a_missing_start_time_means_no():
    """Adjacency is a claim about a timeline; without one it cannot be made."""
    assert not activities_form_one_session(
        [("Ride", None, 60 * 60), _activity("10:15", 60)]
    )


def test_a_missing_duration_means_no():
    assert not activities_form_one_session(
        [("Ride", f"{DATE}T09:00:00Z", None), _activity("10:15", 60)]
    )


def test_three_parts_are_one_session_only_if_every_gap_is_small():
    close = [_activity("09:00", 60), _activity("10:10", 60), _activity("11:20", 60)]
    assert activities_form_one_session(close)

    far = [_activity("09:00", 60), _activity("10:10", 60), _activity("16:00", 60)]
    assert not activities_form_one_session(far)


def test_activities_out_of_order_are_sorted_before_measuring():
    """Callers pass rides in import order, which is not start order."""
    assert activities_form_one_session(
        [_activity("10:15", 60), _activity("09:00", 60)]
    )


def test_the_gap_boundary_is_where_the_constant_says():
    minutes = SPLIT_SESSION_MAX_GAP_SECONDS // 60
    # First ride ends at 10:00; the next starts exactly at the limit, then past it.
    assert activities_form_one_session(
        [_activity("09:00", 60), ("Ride", f"{DATE}T10:00:00Z", 60 * 60)]
    )
    just_over = [
        _activity("09:00", 60),
        ("Ride", f"{DATE}T{10 + (minutes + 1) // 60:02d}:{(minutes + 1) % 60:02d}:00Z",
         60 * 60),
    ]
    assert not activities_form_one_session(just_over)


def test_overlapping_recordings_are_not_rejected_for_overlapping():
    """Overlap is a duplicate question, answered elsewhere — not read as a gap."""
    assert activities_form_one_session(
        [_activity("09:00", 60), _activity("09:30", 60)]
    )


# --- Is this a bike ride at all? (#578) -----------------------------------


@pytest.mark.parametrize(
    ("sport_type", "expected_family"),
    [
        ("WeightTraining", "strength"),
        ("Weight Training", "strength"),
        ("Yoga", "yoga"),
        ("Hike", "hike"),
        ("RockClimbing", "rockclimbing"),
        ("Run", "running"),
    ],
)
def test_a_non_cycling_sport_is_named_with_high_confidence(sport_type, expected_family):
    """The provider stated the sport, so nothing here is inferred. What a gym
    session leaves open is its intensity, not its identity."""
    classification = non_cycling_classification(sport_type)
    assert classification is not None
    purpose, confidence, reason = classification
    assert purpose == expected_family
    assert confidence == "high"
    assert "not a power-based bike ride" in reason


@pytest.mark.parametrize(
    "sport_type",
    ["Ride", "VirtualRide", "MountainBikeRide", "cycling", "Gravel Bike"],
)
def test_a_bike_ride_is_left_to_the_power_classifier(sport_type):
    assert non_cycling_classification(sport_type) is None


@pytest.mark.parametrize("sport_type", [None, "", "   "])
def test_an_unreadable_sport_type_is_a_real_unknown(sport_type):
    """Without a sport there is nothing to assert; inventing one here would only
    move the invention from the workout type to the sport."""
    assert non_cycling_classification(sport_type) is None
