"""Walking as a sport (ai-trainer-ops#182).

The owner's test athlete wanted to walk, cycle when the weather was fine, and
do strength work. Walking was no sport the app knew: the plan could only call a
walk a run, and a walk recorded on Strava matched no planned session.
"""

import pytest

import models
from domain.activity_identity import (
    PLANNABLE_SPORTS,
    SPORT_WALKING,
    activity_family,
    training_sport,
)
from domain.training_load import DEFAULT_LOAD_PER_HOUR
from schemas.plan_day import normalise_plan_sport
from services import coach_schema, opening_message, profile_intent, prompts
from services.illness_return import EASY_TITLES
from services.ride_matching import _session_accepts_ride
from services.run_durability import is_run_activity

ME = "/api/v1/users/me"


@pytest.mark.parametrize("sport_type", ["Walk", "Hike", "walking", "hiking"])
def test_a_walk_or_a_hike_is_walking_to_the_planner(sport_type):
    assert training_sport(sport_type) == SPORT_WALKING
    assert not is_run_activity(sport_type)


def test_the_load_model_still_prices_a_walk_and_a_hike_apart():
    assert activity_family("Walk") == "walk"
    assert activity_family("Hike") == "hike"
    assert DEFAULT_LOAD_PER_HOUR["walk"] < DEFAULT_LOAD_PER_HOUR["hike"]


def test_a_planned_walk_keeps_its_sport():
    assert SPORT_WALKING in PLANNABLE_SPORTS
    assert normalise_plan_sport("walking") == "walking"


@pytest.mark.parametrize(
    ("planned", "recorded", "accepted"),
    [
        ("walking", "Walk", True),
        ("walking", "Hike", True),
        ("walking", "Ride", False),
        ("walking", "Run", False),
        ("cycling", "Walk", False),
        ("running", "Walk", False),
    ],
)
def test_a_walk_fulfils_a_planned_walk_and_nothing_else(planned, recorded, accepted):
    session = {"date": "2026-11-07", "sport": planned, "workoutType": "endurance"}
    ride = models.RideMetric(sport_type=recorded)

    assert _session_accepts_ride(session, ride) is accepted


@pytest.mark.asyncio
async def test_the_profile_takes_walking(client, auth_headers):
    saved = await client.put(
        ME, headers=auth_headers, json={"sports": ["walking", "cycling", "strength"]}
    )

    assert saved.status_code == 200
    assert set(saved.json()["sports"]) == {"walking", "cycling", "strength"}


@pytest.mark.asyncio
async def test_an_unknown_sport_is_still_refused(client, auth_headers):
    reply = await client.put(ME, headers=auth_headers, json={"sports": ["swimming"]})

    assert reply.status_code == 422


def test_the_coach_may_write_walking_and_describes_it_as_a_walk():
    sport = coach_schema._PLAN_UPDATE["properties"]["sport"]
    assert "walking" in sport["enum"]
    assert (
        '"cycling", "running", "strength" or "walking"' in prompts.UNITS_BY_SPORT_RULE
    )
    assert "A walk is a walk or a hike, never a run" in prompts.UNITS_BY_SPORT_RULE
    assert '"walking"' in prompts.generate_plan_system()


@pytest.mark.parametrize(
    ("message", "current", "expected"),
    [
        ("I've started walking.", ["cycling"], ["cycling", "walking"]),
        (
            "Ich habe jetzt auch wandern angefangen.",
            ["cycling"],
            ["cycling", "walking"],
        ),
        ("I stopped hiking.", ["cycling", "walking"], ["cycling"]),
    ],
)
def test_the_chat_adds_and_drops_walking(message, current, expected):
    assert profile_intent._sports(message, current) == expected


def test_walking_has_its_own_words():
    assert opening_message._SHOWN["German"]["walking"] == "Gehen"
    assert opening_message._SHOWN["English"]["walking"] == "Walking"
    assert EASY_TITLES["walking"] == "Easy walk"
