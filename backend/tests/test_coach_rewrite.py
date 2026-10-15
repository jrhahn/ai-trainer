"""A coach reply held to the checks rather than only reported (ai-trainer-ops#87).

The owner's yes to #81 Q26: a reply stating a number the context cannot explain,
or a plan change that breaks a hard planning prior, is written once more with the
finding in the prompt. What survives the rewrite reaches the athlete with a note.
"""

from __future__ import annotations

import json
from datetime import date
from functools import partial
from unittest.mock import patch

import pytest

from services import ai_service, plan_pipeline, plan_priors
from services.llm import AIRateLimitError

PROFILE = {"name": "Test Rider", "weeklyHours": 8, "fitnessLevel": "intermediate"}
PLAN = [
    {
        "date": "2026-04-16",
        "workoutType": "endurance",
        "title": "Endurance Ride",
        "description": "Steady ride",
        "durationMinutes": 60,
    }
]
# No context holds this number, and none composes to it.
INVENTED = "98765"
BACK_TO_BACK = plan_priors.PriorDeparture(
    plan_priors.PRIOR_BACK_TO_BACK,
    plan_priors.EVIDENCE_HEURISTIC,
    "Intensity on consecutive days: 2026-04-16 and 2026-04-17.",
    (date(2026, 4, 16), date(2026, 4, 17)),
)
INTERVALS = {"date": "2026-04-16", "workoutType": "intervals", "title": "VO2", "description": "5x4"}
EASY = {"date": "2026-04-16", "workoutType": "endurance", "title": "Easy", "description": "Z2"}


def _coach(*replies):
    """A coach that gives ``replies`` in turn; records the messages of each call."""
    calls: list[list[dict]] = []

    async def chat(provider, system_prompt, messages, json_mode=False, **kwargs):
        calls.append(messages)
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)

    return chat, calls


def _breaks_on_intervals(updates):
    return [BACK_TO_BACK] if any(u.get("workoutType") == "intervals" for u in updates) else []


async def _ask(chat, question="How does my week look?", **kwargs):
    with patch.object(ai_service, "_chat_history", side_effect=chat):
        return await ai_service.ask_trainer(question, PLAN, PROFILE, **kwargs)


@pytest.mark.asyncio
async def test_a_clean_reply_is_not_written_twice():
    chat, calls = _coach({"response": "Ride steady today.", "planUpdates": []})

    result = await _ask(chat, prior_check=_breaks_on_intervals)

    assert len(calls) == 1
    assert "rewritten" not in result


@pytest.mark.asyncio
async def test_an_invented_number_costs_one_rewrite():
    chat, calls = _coach(
        {"response": f"Your threshold is {INVENTED} W.", "planUpdates": []},
        {"response": "Your threshold looks steady.", "planUpdates": []},
    )

    result = await _ask(chat)

    assert len(calls) == 2
    assert INVENTED in calls[1][-1]["content"]
    assert result["response"] == "Your threshold looks steady."
    assert result["rewritten"] is True


@pytest.mark.asyncio
async def test_a_number_that_survives_the_rewrite_reaches_the_athlete_with_a_note():
    chat, _calls = _coach(
        {"response": f"Your threshold is {INVENTED} W.", "planUpdates": []},
        {"response": f"Still {INVENTED} W.", "planUpdates": []},
    )

    result = await _ask(chat, question="Wie ist meine Schwelle gerade?")

    assert result["response"].startswith(f"Still {INVENTED} W.")
    assert result["response"].endswith(
        f"Hinweis: {INVENTED} in dieser Antwort ließ sich nicht aus deinen Daten ableiten."
    )


@pytest.mark.asyncio
async def test_a_plan_change_breaking_a_hard_prior_costs_one_rewrite():
    chat, calls = _coach(
        {"response": "Intervals today.", "planUpdates": [INTERVALS]},
        {"response": "An easy ride today.", "planUpdates": [EASY]},
    )

    result = await _ask(chat, prior_check=_breaks_on_intervals)

    assert len(calls) == 2
    assert BACK_TO_BACK.statement in calls[1][-1]["content"]
    assert result["plan_updates"] == [EASY]
    assert "Note:" not in result["response"]


@pytest.mark.asyncio
async def test_a_departure_that_survives_is_named_in_the_athletes_language():
    chat, _calls = _coach(
        {"response": "Intervalle heute.", "planUpdates": [INTERVALS]},
        {"response": "Bleibt bei Intervallen.", "planUpdates": [INTERVALS]},
    )

    result = await _ask(chat, question="Kann ich heute Intervalle fahren?", prior_check=_breaks_on_intervals)

    assert result["response"].endswith(
        "Hinweis: Diese Planänderung weicht von einer Planungsregel ab: "
        "zwei harte Tage hintereinander."
    )


@pytest.mark.asyncio
async def test_a_failed_rewrite_keeps_the_first_reply_and_says_what_is_wrong():
    chat, _calls = _coach(
        {"response": "Intervals today.", "planUpdates": [INTERVALS]},
        AIRateLimitError("busy"),
    )

    result = await _ask(chat, prior_check=_breaks_on_intervals)

    assert result["plan_updates"] == [INTERVALS]
    assert result["response"].endswith(
        "Note: this plan change departs from a planning rule: two hard days in a row."
    )


@pytest.mark.asyncio
async def test_an_unreadable_rewrite_keeps_the_first_reply_too():
    chat, _calls = _coach(
        {"response": f"Your threshold is {INVENTED} W.", "planUpdates": []},
        ai_service.AIResponseFormatError("no reply"),
    )

    result = await _ask(chat)

    assert result["response"].startswith(f"Your threshold is {INVENTED} W.")
    assert result["response"].endswith("could not be traced to your data.")


@pytest.mark.asyncio
async def test_a_rewrite_is_counted_with_everything_that_caused_it():
    chat, _calls = _coach(
        {"response": f"Intervals at {INVENTED} W.", "planUpdates": [INTERVALS]},
        {"response": "An easy ride today.", "planUpdates": [EASY]},
    )
    counter = ai_service.metrics.COACH_REWRITES.labels(
        surface="ask_trainer", reason="numbers+priors"
    )
    before = counter._value.get()

    await _ask(chat, prior_check=_breaks_on_intervals)

    assert counter._value.get() == before + 1


def _check(plan):
    return plan_pipeline.update_prior_check(
        partial(plan_priors.find_prior_departures, today=date(2026, 4, 13)), plan
    )


def _day(day: str, workout_type: str) -> dict:
    return {"date": day, "workoutType": workout_type, "title": workout_type, "description": "", "durationMinutes": 60}


def test_an_update_is_held_to_the_departure_it_creates():
    plan = [_day("2026-04-14", "intervals"), _day("2026-04-15", "endurance")]

    found = _check(plan)([{"date": "2026-04-15", "workoutType": "intervals"}])

    assert [d.prior for d in found] == [plan_priors.PRIOR_BACK_TO_BACK]


def test_an_update_is_not_held_to_a_departure_on_days_it_left_alone():
    plan = [
        _day("2026-04-14", "intervals"),
        _day("2026-04-15", "intervals"),
        _day("2026-04-18", "endurance"),
    ]

    assert _check(plan)([{"date": "2026-04-18", "workoutType": "recovery"}]) == []
