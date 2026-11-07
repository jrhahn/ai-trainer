"""A health goal next to a race and general fitness (ai-trainer-ops#182).

The owner's test athlete was "not so much into sports, not on performance
level" and wanted to stay healthy. The plan spoke of a "base block" and "load
week 3 of your base phase", and the week goals of a "Belastungsblock".
"""

from datetime import date

import pytest

import models
from services import opening_message
from services.prompts import (
    generate_plan_user,
    profile_plan_rules,
    ask_trainer_system_sections,
    coach_static_prefix,
    health_goal_voice,
)

ME = "/api/v1/users/me"
ASK = "/api/v1/ai/ask-trainer"


@pytest.mark.asyncio
async def test_the_health_goal_is_stored_and_clears_a_race(client, auth_headers):
    await client.put(
        ME,
        headers=auth_headers,
        json={"trainingGoal": "race", "raceDate": "2027-06-01", "raceDescription": "x"},
    )

    saved = await client.put(ME, headers=auth_headers, json={"trainingGoal": "health"})

    assert saved.status_code == 200
    body = saved.json()
    assert body["trainingGoal"] == "health"
    assert body["raceDate"] is None
    assert body["raceDescription"] is None


@pytest.mark.asyncio
async def test_an_unknown_goal_is_still_refused(client, auth_headers):
    reply = await client.put(
        ME, headers=auth_headers, json={"trainingGoal": "wellness"}
    )

    assert reply.status_code == 422


@pytest.mark.asyncio
async def test_the_chat_reads_the_health_goal(client, auth_headers, mock_ai_service):
    await client.put(ME, headers=auth_headers, json={"trainingGoal": "health"})

    await client.post(
        ASK, headers=auth_headers, json={"question": "Was mache ich heute?"}
    )

    profile = mock_ai_service["ask_trainer"].call_args.args[2]
    assert profile["trainingGoal"] == "health"


def test_the_opening_message_names_the_goal_in_the_athletes_words():
    user = models.User(
        email="werner@example.com",
        hashed_password="x",
        training_goal="health",
        sports=["walking", "cycling"],
    )

    facts = opening_message.build_facts(user, None, date(2026, 11, 7))

    assert facts["answers"]["goal"] == "staying healthy"
    assert opening_message.shown_answers(facts["answers"], "German")["goal"] == (
        "Gesund bleiben"
    )
    assert opening_message.shown_answers(facts["answers"], "English")["goal"] == (
        "Stay Healthy"
    )


def test_the_plan_rules_plan_for_health_in_plain_words():
    rule = profile_plan_rules({"trainingGoal": "health"})
    assert "'trainingGoal' \"health\" (ai-trainer-ops#182)" in rule
    assert "150 to 300 minutes of moderate activity a week" in rule
    assert "major muscle groups on two days" in rule
    assert "From 65" in rule and "balance work" in rule
    assert "no phase, block, load week" in rule
    assert "treating or preventing a disease" in rule


def test_the_coach_speaks_plainly_to_a_health_athlete_only():
    assert "speak in plain" in health_goal_voice({"trainingGoal": "health"})
    assert health_goal_voice({"trainingGoal": "general_fitness"}) == ""
    assert health_goal_voice({}) == ""
    # In the athlete's part of the prompt, not the cached prefix every athlete pays.
    assert "speak in plain" not in coach_static_prefix()


def test_the_chat_prompt_carries_the_line_for_a_health_athlete():
    def sections(goal: str) -> dict:
        return ask_trainer_system_sections(
            {"name": "Werner", "trainingGoal": goal},
            "2026-11-07",
            [],
            [],
            "",
            "",
            "",
            "",
        )

    assert sections("health")["health-goal"].startswith(
        "This athlete wants to stay fit"
    )
    assert sections("race")["health-goal"] == ""


def test_the_profile_rules_go_only_to_the_profiles_that_need_them():
    assert profile_plan_rules({"trainingGoal": "race"}) == ""
    assert profile_plan_rules({}) == ""
    plan_prompt = generate_plan_user(
        {"name": "Werner", "trainingGoal": "health"}, "2026-11-09", ""
    )
    assert "Rules for this athlete's profile:" in plan_prompt
    assert "Rules for this athlete's profile:" not in generate_plan_user(
        {"name": "Lena", "trainingGoal": "race"}, "2026-11-09", ""
    )


def test_a_switch_to_the_health_goal_rebuilds_the_plan():
    from services import plan_inputs
    from tests.test_plan_inputs_158 import BASE

    before = {**BASE, "goal": "general_fitness"}

    assert plan_inputs.reasons(before, {**BASE, "goal": "health"}) == ["goal"]
    assert "goal" in plan_inputs.ATHLETE_REASONS
    # A race coming or going is the race's reason, not a second one.
    assert "goal" not in plan_inputs.reasons(
        {**before, "goal": "race"}, {**BASE, "goal": "health"}
    )
    # A record from before the goal was kept is not rebuilt for it.
    assert plan_inputs.reasons(BASE, {**BASE, "goal": "health"}) == []
