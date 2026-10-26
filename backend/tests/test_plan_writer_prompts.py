"""The plan writers: generate_training_plan and adapt_training_plan, their system prompts and the race context.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations

import json
import datetime
from unittest.mock import patch

import pytest

import services.ai_service as ai_service


def _a_fortnight_from(days: list[dict]) -> list[dict]:
    """``days`` padded with rest days to the fortnight a plan has to be.

    A generated plan must cover the fortnight from today (ai-trainer-ops#113);
    these fixtures predate that and held one to three days.
    """
    start = datetime.date.fromisoformat(days[0]["date"])
    have = {day["date"] for day in days}
    padded = list(days)
    for offset in range(14):
        on = (start + datetime.timedelta(days=offset)).isoformat()
        if on not in have:
            padded.append(
                {"date": on, "workoutType": "rest", "title": "Rest", "description": "",
                 "durationMinutes": 0}
            )
    return sorted(padded, key=lambda day: day["date"])


def _today_is(iso: str):
    return patch.object(ai_service, "app_today_iso", lambda timezone_name=None: iso)


@pytest.mark.asyncio
async def test_generate_training_plan_calls_chat():
    """generate_training_plan should return the plan list from AI response."""
    profile = {
        "bikeType": "road",
        "trainingGoal": "general_fitness",
        "fitnessLevel": "intermediate",
    }
    fake_plan = [
        {
            "date": "2026-04-10",
            "workoutType": "endurance",
            "title": "Endurance",
            "description": "Long ride",
            "durationMinutes": 90,
        }
    ]

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"plan": _a_fortnight_from(fake_plan)})

    with patch.object(ai_service, "_chat", side_effect=fake_chat), _today_is("2026-04-10"):
        result = await ai_service.generate_training_plan(profile, provider="openai")

    assert result[0] == fake_plan[0]
    assert len(result) == 14


@pytest.mark.asyncio
async def test_generate_training_plan_repairs_invalid_day():
    """A day that fails PlanDay validation triggers a re-prompt, then succeeds (#422)."""
    profile = {"bikeType": "road", "trainingGoal": "general_fitness",
               "fitnessLevel": "intermediate"}
    invalid = {"date": "2026-04-10", "workoutType": "endurance", "title": "x",
               "description": "y", "durationMinutes": "lots"}  # non-numeric duration
    valid = [{"date": "2026-04-10", "workoutType": "endurance", "title": "x",
              "description": "y", "durationMinutes": 90}]
    prompts: list[str] = []

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        prompts.append(user_msg)
        payload = (
            {"plan": [invalid]} if len(prompts) == 1 else {"plan": _a_fortnight_from(valid)}
        )
        return json.dumps(payload)

    with patch.object(ai_service, "_chat", side_effect=fake_chat), _today_is("2026-04-10"):
        result = await ai_service.generate_training_plan(profile, provider="openai")

    assert len(prompts) == 2  # retried once
    assert result[0]["durationMinutes"] == 90
    assert "invalid" in prompts[1].lower()  # correction fed back to the model


@pytest.mark.asyncio
async def test_generate_training_plan_gives_up_after_max_attempts():
    """After the repair budget is exhausted the caller gets a format error, not a 500."""
    profile = {"bikeType": "road", "trainingGoal": "general_fitness",
               "fitnessLevel": "intermediate"}
    invalid = {"date": "2026-04-10", "durationMinutes": "lots"}
    calls = 0

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        nonlocal calls
        calls += 1
        return json.dumps({"plan": [invalid]})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        with pytest.raises(ai_service.AIResponseFormatError):
            await ai_service.generate_training_plan(profile, provider="openai")

    assert calls == ai_service._MAX_PLAN_VALIDATION_ATTEMPTS


@pytest.mark.asyncio
async def test_adapt_training_plan_calls_chat():
    """adapt_training_plan should merge AI updated days into the plan."""
    plan = [
        {
            "date": "2026-04-10",
            "workoutType": "endurance",
            "title": "Endurance",
            "description": "Long ride",
            "durationMinutes": 90,
            "completed": False,
        }
    ]
    recent_feedback = [
        {
            "actualDurationMinutes": 60,
            "perceivedEffort": 5,
            "notes": "Very hard",
            "completedAt": "2026-04-09T10:00:00Z",
        }
    ]
    profile = {"fitnessLevel": "intermediate"}
    updated_day = {
        "date": "2026-04-10",
        "workoutType": "recovery",
        "title": "Recovery",
        "description": "Easy spin",
        "durationMinutes": 45,
    }

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"updatedDays": [updated_day]})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.adapt_training_plan(
            plan, recent_feedback, profile, provider="openai"
        )

    assert result[0]["workoutType"] == "recovery"


# ---------------------------------------------------------------------------
# adapt_plan_system includes TRAINING_PLAN_PRINCIPLES (Task 3)
# ---------------------------------------------------------------------------


def test_adapt_plan_system_includes_training_principles():
    """adapt_plan_system must embed TRAINING_PLAN_PRINCIPLES like generate_plan_system does."""
    from services.prompts import TRAINING_PLAN_PRINCIPLES, adapt_plan_system

    assert TRAINING_PLAN_PRINCIPLES in adapt_plan_system()


def test_adapt_plan_system_includes_tsb_guidance():
    from services.prompts import adapt_plan_system
    system = adapt_plan_system()
    assert "TSB" in system
    assert "fatigue" in system.lower() or "recovery" in system.lower()


def test_adapt_plan_system_preserves_hard_schedule_constraints():
    from services.prompts import adapt_plan_system

    system = adapt_plan_system().lower()

    assert "hard athlete constraints" in system
    assert "unavailable" in system
    assert "do not schedule training" in system
    assert "physiologically optimal" in system


def test_adapt_plan_system_includes_hard_session_spacing_rules():
    from services.prompts import adapt_plan_system

    system = adapt_plan_system().lower()

    assert "hard-session spacing rules" in system
    assert "actual activity history is authoritative" in system
    assert "inspect the recent activity history first" in system
    assert "within roughly 48 hours" in system
    assert "do not keep, recommend, or create another vo2max" in system
    assert "use planupdates" in system


# ---------------------------------------------------------------------------
# onboarding race context prompts
# ---------------------------------------------------------------------------


def test_race_profile_context_empty_without_race_date_or_description():
    from services.prompts import race_profile_context_section

    assert race_profile_context_section({"trainingGoal": "general_fitness"}) == ""


def test_race_profile_context_includes_race_date_and_description():
    from datetime import date

    from services.prompts import race_profile_context_section

    section = race_profile_context_section(
        {
            "trainingGoal": "general_fitness",
            "raceDate": "2026-09-15",
            "raceDescription": "Local gran fondo with repeated climbs",
        },
        date(2026, 9, 1),  # before race day; a past race is history (ops#71)
    )

    assert "Profile race context" in section
    assert "Race date: 2026-09-15" in section
    assert "race-specific preparation and taper timing" in section
    assert "Local gran fondo with repeated climbs" in section
    assert "event context for training decisions" in section


def test_generate_adapt_and_chat_prompts_include_profile_race_context():
    from services.prompts import (
        adapt_plan_user,
        ask_trainer_plan_updates_rule,
        ask_trainer_system,
        generate_plan_user,
    )

    profile = {
        "trainingGoal": "general_fitness",
        "raceDate": "2026-09-15",
        "raceDescription": "Flat time trial with crosswinds",
        "fitnessLevel": "intermediate",
    }
    generate_prompt = generate_plan_user(
        profile,
        today="2026-05-10",
        assessment_section="",
    )
    adapt_prompt = adapt_plan_user(
        profile,
        today="2026-05-10",
        recent_feedback=[],
        incomplete_days=[],
    )
    chat_prompt = ask_trainer_system(
        profile=profile,
        today="2026-05-10",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    for prompt in (generate_prompt, adapt_prompt, chat_prompt):
        assert "Profile race context" in prompt
        assert "Flat time trial with crosswinds" in prompt
        assert "Onboarding goal priority" not in prompt


def test_generate_plan_user_omits_race_context_for_general_fitness():
    from services.prompts import generate_plan_user

    prompt = generate_plan_user(
        {
            "trainingGoal": "general_fitness",
            "fitnessLevel": "intermediate",
        },
        today="2026-05-10",
        assessment_section="",
    )

    assert "Profile race context" not in prompt
    assert "Onboarding goal priority" not in prompt


def test_race_events_context_keeps_fixed_distance_elevation_commitments():
    from services.prompts import race_events_context_section

    section = race_events_context_section(
        [
            {
                "date": "2026-09-15",
                "startTime": "08:30",
                "distanceKm": 145.0,
                "elevationM": 2400,
            }
        ]
    )

    assert "Race calendar (fixed athlete events to plan around)" in section
    assert "2026-09-15 at 08:30: 145 km with 2400 m climbing" in section
    assert "Treat these events as real calendar commitments" in section
    assert "terrain-specific work" in section


# ---------------------------------------------------------------------------
# generate_training_plan — workoutPurpose and keyFocusPoints fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_plan_workout_purpose_and_focus_points_present():
    """Every non-rest day returned by generate_training_plan must have workoutPurpose
    (non-empty string) and keyFocusPoints (list with at least 3 entries)."""
    profile = {
        "bikeType": "road",
        "trainingGoal": "general_fitness",
        "fitnessLevel": "intermediate",
        "currentFTP": 260,
    }
    fake_plan = [
        {
            "date": "2026-04-14",
            "workoutType": "endurance",
            "title": "Zone 2 Endurance",
            "description": "Ride 90 min at 195–220 W (Zone 2, 75–85% of your 260 W FTP). Keep HR under 148 bpm.",
            "durationMinutes": 90,
            "targetPower": {"low": 195, "high": 220},
            "workoutPurpose": "Builds aerobic base and improves fat oxidation. Placed early in the week to accumulate low-intensity volume before harder sessions.",
            "keyFocusPoints": [
                "Keep cadence between 88–95 rpm throughout",
                "HR must stay below 148 bpm; back off on any climbs if it creeps higher",
                "Breathe comfortably — you should be able to hold a conversation",
            ],
        },
        {
            "date": "2026-04-15",
            "workoutType": "rest",
            "title": "Rest Day",
            "description": "Complete rest.",
            "durationMinutes": 0,
        },
        {
            "date": "2026-04-16",
            "workoutType": "intervals",
            "title": "VO2max Intervals",
            "description": "5×4 min at 312–338 W (120–130% of your 260 W FTP) with 4 min easy recovery between reps.",
            "durationMinutes": 70,
            "targetPower": {"low": 312, "high": 338},
            "workoutPurpose": "Raises VO2max by stressing the cardiovascular system at near-maximal intensity. Positioned mid-week after a rest day for maximum freshness.",
            "keyFocusPoints": [
                "Start each interval at a rolling pace, not a standing sprint",
                "Target 90–95 rpm cadence during the hard efforts",
                "If HR climbs above 185 bpm before the interval ends, back off slightly",
                "Recovery spins should stay below 130 W",
            ],
        },
    ]

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"plan": _a_fortnight_from(fake_plan)})

    with patch.object(ai_service, "_chat", side_effect=fake_chat), _today_is("2026-04-14"):
        result = await ai_service.generate_training_plan(profile, provider="openai")

    non_rest_days = [day for day in result if day.get("workoutType") != "rest"]
    assert len(non_rest_days) > 0, "Expected at least one non-rest day in the plan"
    for day in non_rest_days:
        assert day.get("workoutPurpose"), (
            f"Day {day['date']} ({day['workoutType']}) is missing workoutPurpose"
        )
        focus_points = day.get("keyFocusPoints", [])
        assert isinstance(focus_points, list) and len(focus_points) >= 3, (
            f"Day {day['date']} ({day['workoutType']}) must have at least 3 keyFocusPoints, "
            f"got {len(focus_points)}"
        )


# ---------------------------------------------------------------------------
# workoutPurpose points forwards (#623)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", ["generate_plan_system", "adapt_plan_system"])
def test_workout_purpose_is_specified_as_what_the_session_earns(builder):
    """The athlete reads this heading before riding, not while auditing the plan.

    It used to be specified as "the physiological goal of this session and why it
    is placed here in the plan", with an example that recapped the day before —
    which put a justification of the planner under a heading the athlete opens
    to find out what the next hour buys them.  ``plan_allocation_rule`` already
    argued the opposite ("write what the day buys them"), so the two specs
    disagreed with each other.
    """
    from services import prompts

    text = getattr(prompts, builder)()
    purpose = text[text.index('"workoutPurpose"') :][:600].lower()

    assert "earns the athlete" in purpose
    assert "forwards" in purpose
    assert "why it is placed here" not in purpose


@pytest.mark.asyncio
async def test_adapt_training_plan_incomplete_days_include_weekday_labels(monkeypatch):
    """adapt_training_plan() must enrich incomplete_days with weekday/dateLabel/relativeDay
    before sending to the prompt.

    Without enrichment the model receives raw ISO dates and must infer weekday names
    itself — a known error source when reasoning about rescheduling.
    """
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 23)
    )
    monkeypatch.setattr(
        ai_service, "app_today_iso", lambda timezone_name=None: "2026-06-23"
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, task="plan"):
        captured["user_msg"] = user_msg
        return json.dumps({"updatedDays": []})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    await ai_service.adapt_training_plan(
        plan=[
            {"date": "2026-06-22", "workoutType": "strength", "title": "Upper Body", "durationMinutes": 45, "completed": True},
            {"date": "2026-06-23", "workoutType": "rest", "title": "Rest Day", "durationMinutes": 0},
            {"date": "2026-06-24", "workoutType": "vo2max", "title": "VO2 Max", "durationMinutes": 60},
        ],
        recent_feedback=[],
        profile={},
        timezone_name="Europe/Berlin",
    )

    user_msg = str(captured["user_msg"])
    # The completed day should be excluded from incomplete_days
    assert '"date": "2026-06-22"' not in user_msg or '"completed": true' not in user_msg
    # Today's incomplete entry must carry weekday and relativeDay
    assert '"weekday": "Monday"' in user_msg or '"weekday": "Tuesday"' in user_msg
    assert '"relativeDay": "today"' in user_msg
    # Tomorrow's entry must carry relativeDay
    assert '"relativeDay": "tomorrow"' in user_msg
