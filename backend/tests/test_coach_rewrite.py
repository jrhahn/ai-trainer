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
async def test_a_duration_the_reply_s_own_plan_change_sets_is_explained():
    """A prescription, not an invention (ai-trainer-ops#103 Q31, the owner's (a)).
    The scenario run of 2026-10-16 got "Hinweis: 75 …" for exactly this."""
    found = []
    real = ai_service.output_audit.report

    def report(text, context=None, *, surface):
        findings = real(text, context, surface=surface)
        found.extend(findings)
        return findings

    chat, calls = _coach(
        {
            "response": "Heute 75 Minuten locker, also 1,25 Stunden.",
            "planUpdates": [{**EASY, "durationMinutes": 75}],
        }
    )

    with patch.object(ai_service.output_audit, "report", report):
        result = await _ask(chat, question="Passt der Plan heute?")

    assert len(calls) == 1
    assert "Hinweis" not in result["response"]
    assert found == []


@pytest.mark.asyncio
async def test_the_same_duration_without_a_plan_change_is_still_a_finding():
    reply = {"response": "Fahr heute 75 Minuten locker.", "planUpdates": []}
    chat, calls = _coach(reply, reply)

    result = await _ask(chat, question="Passt der Plan heute?")

    assert len(calls) == 2
    assert result["response"].endswith(
        "Hinweis: 75 in dieser Antwort ließ sich nicht aus deinen Daten ableiten."
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


# --- a race prior left standing, a change claimed but not carried -------------
# ai-trainer-ops#103 Q33, the owner's (a). Run 1478 named the long ride the day
# before the race, asked "Wollen wir …?" and changed nothing. It also answered
# "Das machen wir" to "Bitte mach Freitag zum Ruhetag" and changed nothing.

RACE = date(2026, 4, 17)
LONG_EVE = {**_day("2026-04-16", "endurance"), "durationMinutes": 240}
SHORT_EVE = {"date": "2026-04-16", "workoutType": "recovery", "title": "Spin", "description": "", "durationMinutes": 45}
REST_FRIDAY = {"date": "2026-04-17", "workoutType": "rest", "title": "Rest day", "description": ""}


def _race_check(plan, race=RACE):
    return plan_pipeline.update_prior_check(
        partial(plan_priors.find_prior_departures, today=date(2026, 4, 13), race_dates=[race]),
        plan,
        today=date(2026, 4, 13),
    )


def test_with_today_a_race_eve_is_held_with_no_update_at_all():
    found = _race_check([LONG_EVE])([])

    assert [d.prior for d in found] == [plan_priors.PRIOR_RACE_EVE]


def test_without_today_no_update_is_held_to_anything():
    check = plan_pipeline.update_prior_check(
        partial(plan_priors.find_prior_departures, today=date(2026, 4, 13), race_dates=[RACE]),
        [LONG_EVE],
    )

    assert check([]) == []


def test_a_race_more_than_a_week_away_is_not_held_on_days_left_alone():
    plan = [{**_day("2026-04-24", "endurance"), "durationMinutes": 240}]

    assert _race_check(plan, race=date(2026, 4, 25))([]) == []


def test_only_the_race_priors_are_held_on_days_left_alone():
    plan = [_day("2026-04-14", "intervals"), _day("2026-04-15", "intervals")]

    assert _race_check(plan)([]) == []


@pytest.mark.asyncio
async def test_a_race_prior_the_reply_leaves_standing_costs_one_rewrite():
    chat, calls = _coach(
        {"response": "Thursday stays your long ride.", "planUpdates": []},
        {"response": "I've cut Thursday to an easy spin.", "planUpdates": [SHORT_EVE]},
    )

    result = await _ask(chat, prior_check=_race_check([LONG_EVE]))

    assert len(calls) == 2
    request = calls[1][-1]["content"]
    assert "in the 2 days before the race on 2026-04-17" in request
    assert "This is a protective change" in request
    assert result["plan_updates"] == [SHORT_EVE]
    assert "Note:" not in result["response"]


@pytest.mark.asyncio
async def test_a_race_prior_still_standing_after_the_rewrite_gets_its_own_note():
    reply = {"response": "Donnerstag bleibt die lange Ausfahrt.", "planUpdates": []}
    chat, _calls = _coach(reply, reply)

    result = await _ask(
        chat, question="Wie sieht meine Woche aus?", prior_check=_race_check([LONG_EVE])
    )

    assert result["response"].endswith(
        "Hinweis: Dein Plan für die nächsten Tage weicht von einer Planungsregel ab: "
        "eine lange Einheit kurz vor dem Rennen."
    )


@pytest.mark.asyncio
async def test_a_change_the_reply_claims_but_does_not_carry_costs_one_rewrite():
    chat, calls = _coach(
        {"response": "Das machen wir — Freitag halten wir komplett frei.", "planUpdates": []},
        {"response": "Freitag ist jetzt ein Ruhetag.", "planUpdates": [REST_FRIDAY]},
    )

    result = await _ask(chat, question="Bitte mach Freitag zum Ruhetag.")

    assert len(calls) == 2
    assert '("Das machen wir — Freitag halten wir komplett frei.")' in calls[1][-1]["content"]
    assert result["plan_updates"] == [REST_FRIDAY]
    assert "Hinweis" not in result["response"]


@pytest.mark.asyncio
async def test_a_claim_that_survives_the_rewrite_gets_a_note():
    reply = {"response": "Ich habe den Freitag auf einen Ruhetag geändert.", "planUpdates": []}
    chat, _calls = _coach(reply, reply)

    result = await _ask(chat, question="Bitte mach Freitag zum Ruhetag.")

    assert result["response"].endswith(
        "Hinweis: Diese Antwort nennt eine Planänderung, die dein Plan nicht enthält."
    )


@pytest.mark.asyncio
async def test_a_ride_label_update_carries_the_change_it_claims():
    chat, calls = _coach(
        {
            "response": "I've marked today's ride as OK.",
            "planUpdates": [],
            "ride_label_update": {"activity_date": "2026-04-16", "label": "OK"},
        }
    )

    await _ask(chat, question="Set today's ride to OK.")

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_claim_is_counted_as_a_reason_of_its_own():
    chat, _calls = _coach(
        {"response": "Ich habe den Freitag auf einen Ruhetag geändert.", "planUpdates": []},
        {"response": "Freitag bleibt, wie er ist.", "planUpdates": []},
    )
    counter = ai_service.metrics.COACH_REWRITES.labels(surface="ask_trainer", reason="claim")
    before = counter._value.get()

    await _ask(chat, question="Bitte mach Freitag zum Ruhetag.")

    assert counter._value.get() == before + 1


@pytest.mark.parametrize(
    "text",
    [
        "Ich habe den Freitag im Plan auf einen Ruhetag geändert.",
        "Das machen wir — nächsten Freitag halten wir komplett frei.",
        "Alles klar, das machen wir am Freitag.",
        "I’ve moved your intervals to Thursday.",
        "Saturday is now a complete rest day for you.",
    ],
)
def test_a_change_reported_as_made_is_a_claim(text):
    assert ai_service._claimed_change(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Soll ich den Freitag zum Ruhetag machen?",
        "Ich habe den Plan für Freitag nicht geändert.",
        "Ich habe gesehen, dass du die Einheit am Freitag verschoben hast.",
        # The chat route sets a stated FTP itself (ai-trainer-ops#70).
        "I've updated your FTP to 285 W, so tomorrow's intervals use it.",
        "Ich habe meine Einschätzung deiner Erholung angepasst.",
        "Das machen wir am besten so: zehn Minuten einrollen, dann die Intervalle.",
        # An announcement is not matched: in the owner's chat export the present
        # tense caught replies that changed nothing and promised nothing.
        "Lass uns den Freitag zum Ruhetag machen.",
    ],
)
def test_an_offer_a_negation_or_someone_elses_change_is_no_claim(text):
    assert ai_service._claimed_change(text) is None
