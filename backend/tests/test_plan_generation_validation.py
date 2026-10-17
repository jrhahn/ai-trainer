"""A generated plan is a usable fortnight, or an error (ai-trainer-ops#113).

Before this, a reply with the days under another key or with an empty list was
read as an empty plan, saved, and onboarding ended with HTTP 200 and nothing to
train. Prose and a bare list were a 500, and impossible days persisted.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

import schemas
from services import ai_service, plan_maintenance

TODAY = date(2026, 10, 19)


def _fortnight(**overrides) -> list[dict]:
    return [
        {
            "date": (TODAY + timedelta(days=offset)).isoformat(),
            "workoutType": "endurance",
            "title": "Easy ride",
            "description": "Z2",
            "durationMinutes": 60,
            **overrides,
        }
        for offset in range(14)
    ]


def _model(*replies: object):
    calls: list[str] = []

    async def chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        calls.append(user_msg)
        reply = replies[len(calls) - 1]
        return reply if isinstance(reply, str) else json.dumps(reply)

    return chat, calls


async def _generate(*replies: object) -> tuple[list[dict], list[str]]:
    chat, calls = _model(*replies)
    with patch.object(ai_service, "_chat", side_effect=chat):
        plan = await ai_service._generate_plan_days(
            "gemini", "system", "user", today=TODAY.isoformat()
        )
    return plan, calls


# ---------------------------------------------------------------------------
# Reading the reply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("parsed", "found"),
    [
        ([{"date": "2026-10-19"}], True),
        ({"plan": [{"date": "2026-10-19"}]}, True),
        ({"trainingPlan": [{"date": "2026-10-19"}]}, True),
        ({"days": [{"date": "2026-10-19"}], "notes": ["x"]}, True),
        ({"a": [{"date": "2026-10-19"}], "b": [{"date": "2026-10-20"}]}, False),
        ({"plan": "none"}, False),
        ("a plan", False),
        (None, False),
    ],
)
def test_the_days_are_found_where_the_model_put_them(parsed, found):
    assert (ai_service._plan_days_from(parsed) is not None) is found


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_reply",
    [
        {"plan": []},
        {"summary": "Here is your plan."},
        "Here is your training plan for the next two weeks: ...",
    ],
)
async def test_a_reply_without_days_is_asked_again(first_reply):
    plan, calls = await _generate(first_reply, {"plan": _fortnight()})

    assert len(plan) == 14
    assert len(calls) == 2
    assert "held no plan" in calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [{"trainingPlan": _fortnight()}, _fortnight()])
async def test_the_days_under_another_key_or_bare_are_taken_at_once(reply):
    plan, calls = await _generate(reply)

    assert len(plan) == 14
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_no_plan_three_times_is_an_error_not_an_empty_plan():
    with pytest.raises(ai_service.AIResponseFormatError):
        await _generate({"plan": []}, "prose", {"plan": []})


# ---------------------------------------------------------------------------
# A fortnight from today
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bad_day", "said"),
    [
        ({"date": "19.10.2026"}, "is not a date"),
        ({"date": "2026-10-5"}, "is not a date"),
        ({"date": "2025-10-20"}, "is outside"),
        ({"durationMinutes": -30}, "has 0 minutes"),
        ({"durationMinutes": 0}, "has 0 minutes"),
        ({"durationMinutes": 6000}, "has 6000 minutes"),
    ],
)
async def test_an_impossible_day_is_named_and_asked_again(bad_day, said):
    broken = _fortnight()
    broken[3] = {**broken[3], **bad_day}

    plan, calls = await _generate({"plan": broken}, {"plan": _fortnight()})

    assert len(plan) == 14
    assert len(calls) == 2
    assert said in calls[1]


@pytest.mark.asyncio
async def test_a_rest_day_needs_no_minutes():
    days = _fortnight()
    days[2] = {**days[2], "workoutType": "rest", "durationMinutes": 0}

    plan, calls = await _generate({"plan": days})

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_too_few_days_is_no_plan():
    plan, calls = await _generate({"plan": _fortnight()[:3]}, {"plan": _fortnight()})

    assert len(calls) == 2
    assert "it has 3 days" in calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN"])
async def test_a_non_finite_duration_is_asked_again_not_a_500(value):
    """json.loads reads these as floats; round(inf) raised an OverflowError."""
    broken = json.dumps({"plan": _fortnight()}).replace(
        '"durationMinutes": 60', f'"durationMinutes": {value}', 1
    )

    plan, calls = await _generate(broken, {"plan": _fortnight()})

    assert len(plan) == 14
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_float_minutes_are_rounded_rather_than_rejected():
    plan, calls = await _generate({"plan": _fortnight(durationMinutes=90.7)})

    assert len(calls) == 1
    assert {day["durationMinutes"] for day in plan} == {91}


# ---------------------------------------------------------------------------
# The route never saves an empty plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_route_refuses_to_save_an_empty_plan(client, auth_headers, mock_ai_service):
    mock_ai_service["generate_training_plan"].return_value = []

    response = await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})

    assert response.status_code == 502
    stored = await client.get("/api/v1/users/me/plan", headers=auth_headers)
    assert stored.json()["plan"] == []


# ---------------------------------------------------------------------------
# Targets and durations no plan day may hold
# ---------------------------------------------------------------------------


def _day(**fields) -> schemas.PlanDay:
    return schemas.PlanDay.model_validate(
        {"date": "2026-10-20", "workoutType": "intervals", "title": "VO2", **fields}
    )


def test_a_target_with_its_bounds_reversed_is_put_in_order():
    assert _day(targetPower={"low": 300, "high": 200}).target_power.model_dump() == {
        "low": 200,
        "high": 300,
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"targetPower": 0.75},
        {"targetPower": {"low": 0.65, "high": 0.75}},
        {"targetPower": {"low": 250, "high": 2500}},
        {"targetHeartRate": {"low": 950, "high": 1200}},
        {"targetHeartRate": {"low": 30, "high": 140}},
    ],
)
def test_an_impossible_target_is_dropped(fields):
    day = _day(**fields)

    assert day.target_power is None
    assert day.target_heart_rate is None


@pytest.mark.parametrize(
    "fields",
    [
        {"targetPower": float("inf")},
        {"targetPower": {"low": float("inf"), "high": 200}},
        {"targetPower": {"low": float("nan"), "high": 200}},
        {"targetHeartRate": "inf"},
        {"targetHeartRate": "Infinity"},
        {"targetPower": "lots"},
    ],
)
def test_a_target_that_cannot_be_read_is_dropped_not_a_500(fields):
    """int(inf) raised an OverflowError pydantic does not catch (review of #926)."""
    day = _day(**fields)

    assert day.target_power is None
    assert day.target_heart_rate is None


@pytest.mark.asyncio
async def test_an_infinite_target_in_a_reply_is_no_500():
    broken = json.dumps({"plan": _fortnight(targetPower={"low": 200, "high": 220})}).replace(
        '"high": 220', '"high": Infinity', 1
    )

    plan, calls = await _generate(broken)

    assert len(calls) == 1
    assert "targetPower" not in plan[0]


def test_a_plausible_target_is_kept():
    day = _day(targetPower={"low": 230, "high": 250}, targetHeartRate={"low": 140, "high": 155})

    assert day.target_power.model_dump() == {"low": 230, "high": 250}
    assert day.target_heart_rate.model_dump() == {"low": 140, "high": 155}


def test_minutes_are_whole_and_never_negative():
    assert _day(durationMinutes=90.7).duration_minutes == 91
    assert _day(durationMinutes=-30).duration_minutes == 0
    window = _day(durationMinMinutes=59.6, durationMaxMinutes=90.2)
    assert (window.duration_min_minutes, window.duration_max_minutes) == (60, 90)


# ---------------------------------------------------------------------------
# What the nightly job adds
# ---------------------------------------------------------------------------


def test_nightly_additions_are_real_days_with_their_own_slots():
    plan = [{"date": "2026-10-20", "workoutType": "endurance", "title": "E", "durationMinutes": 60}]
    additions = [
        {"date": "2026-10-25", "workoutType": "intervals", "title": "A", "durationMinutes": 60,
         "targetPower": {"low": 0.65, "high": 0.75}},
        {"date": "2026-10-25", "workoutType": "endurance", "title": "B", "durationMinutes": 45},
        {"date": "2026-10-2x", "workoutType": "endurance", "title": "C", "durationMinutes": 45},
    ]

    result = plan_maintenance.apply_maintenance_updates(plan, additions, "2026-10-20")

    added = [d for d in result if d["date"] == "2026-10-25"]
    assert [(d["title"], d.get("slot", 0)) for d in added] == [("A", 0), ("B", 1)]
    assert "targetPower" not in added[0]
    assert all(d["date"] != "2026-10-2x" for d in result)


def test_a_dropped_addition_leaves_the_patches_and_other_additions():
    """Review of #926: dropping one addition must cost nothing else."""
    plan = [{"date": "2026-10-20", "workoutType": "endurance", "title": "E", "durationMinutes": 60}]
    updates = [
        {"date": "2026-10-20", "title": "Patched"},
        {"date": "2026-10-2x", "workoutType": "endurance", "title": "Bad", "durationMinutes": 45},
        {"date": "2026-10-26", "workoutType": "intervals", "title": "Kept", "durationMinutes": 60,
         "targetHeartRate": {"low": 950, "high": 1200}},
    ]

    result = plan_maintenance.apply_maintenance_updates(plan, updates, "2026-10-20")

    by_date = {d["date"]: d for d in result}
    assert by_date["2026-10-20"]["title"] == "Patched"
    assert by_date["2026-10-26"]["title"] == "Kept"
    # Stored with its impossible target dropped, not rejected.
    assert "targetHeartRate" not in by_date["2026-10-26"]
    assert set(by_date) == {"2026-10-20", "2026-10-26"}
