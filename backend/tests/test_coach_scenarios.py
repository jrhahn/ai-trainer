"""The scenario harness itself: its checks catch the chat's failures (ai-trainer-ops#76).

The scenarios only mean something if each check fails on the reply the coach
actually gave in the owner's chat and passes on a sound one. Those replies are
paraphrased here; the real model is never called.
"""

from datetime import date

import pytest

from config import settings
from scripts import coach_scenarios as cs

TODAY = date(2026, 10, 7)  # a Wednesday; Friday is 2026-10-09
FRIDAY = "2026-10-09"
TOMORROW = "2026-10-08"

GOOD = {
    "past-race": (
        "Heute wäre eine ruhige Grundlage von zwei Stunden ideal, das baut deine Ausdauer für die Trails.",
        [],
    ),
    "current-ftp": (
        "Für Z2 peilst du heute 160 bis 210 W an, bei deinem FTP von 285 W.",
        [],
    ),
    "tomorrow-off": (
        "Kein Problem, wir legen die Intervalle auf Freitag und morgen ist frei.",
        [{"date": TOMORROW, "workoutType": "rest"}],
    ),
    "today-is-today": (
        "Heute, mittwoch, steht deine lange Ausfahrt mit 210 Minuten an.",
        [],
    ),
    "fresh-after-rest": (
        "After two rest days a sweet spot session today makes sense; keep it to 2x15.",
        [{"date": "2026-10-07", "workoutType": "sweet_spot"}],
    ),
    "trail-motivation": (
        "Nimm das MTB, die Trails halten dich bei Laune und Grundlage geht auch dort.",
        [],
    ),
    "claims-match-changes": (
        "Erledigt, ich habe den Freitag zum Ruhetag gemacht.",
        [{"date": FRIDAY, "workoutType": "rest"}],
    ),
}

# What the coach said in the chat, paraphrased, and the check it must fail.
BAD = {
    "past-race": (
        "Für dein 80km-Rennen im Mai ist eine lange Grundlage heute das Richtige.",
        "does not plan toward the race that is over",
    ),
    "current-ftp": (
        "Bei deinem FTP von 320 W liegt Z2 bei 176 bis 240 W.",
        "does not use the old 320 W FTP",
    ),
    "tomorrow-off": (
        "Wir lassen die Intervalle morgen.",
        "takes the intervals off tomorrow",
    ),
    "today-is-today": (
        "Heute, dienstag, ist ein Ruhetag.",
        "does not call today another weekday than mittwoch",
    ),
    "fresh-after-rest": (
        "Your body still needs recovery, so today stays easy.",
        "engages with the sweet spot request",
    ),
    "trail-motivation": (
        "Nimm das Rennrad, damit trainierst du sauberer als mit dem MTB.",
        "recommends the MTB",
    ),
    "claims-match-changes": (
        "Ich habe den Freitag angepasst.",
        "makes Friday a rest day in the plan update",
    ),
}


def _fake(replies: dict):
    by_question = {s.question: s.name for s in cs.scenarios(TODAY)}

    async def ask_trainer(question, plan, profile, **_kwargs):
        reply, updates = replies[by_question[question]]
        return {"response": reply, "plan_updates": updates}

    return ask_trainer


@pytest.mark.asyncio
async def test_every_scenario_passes_a_sound_reply(monkeypatch):
    monkeypatch.setattr(cs.ai_service, "ask_trainer", _fake(GOOD))

    outcomes = await cs.run("gemini", today=TODAY)

    assert {s.name: failed for s, _r, failed, _e in outcomes} == {
        name: [] for name in GOOD
    }


@pytest.mark.asyncio
async def test_every_scenario_catches_the_reply_the_chat_got(monkeypatch):
    bad = {name: (reply, []) for name, (reply, _check) in BAD.items()}
    monkeypatch.setattr(cs.ai_service, "ask_trainer", _fake(bad))

    outcomes = await cs.run("gemini", today=TODAY)

    for scenario, _result, failed, _error in outcomes:
        assert BAD[scenario.name][1] in failed, scenario.name


@pytest.mark.asyncio
async def test_a_failing_call_is_reported_not_raised_and_the_report_renders(
    monkeypatch,
):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(cs.ai_service, "ask_trainer", broken)

    report = cs.render(await cs.run("gemini", only="past-race", today=TODAY))

    assert "0 of 1 passed" in report
    assert "error: RuntimeError: provider down" in report


@pytest.mark.asyncio
async def test_the_whole_run_goes_through_the_stub_provider(monkeypatch):
    monkeypatch.setattr(settings, "ai_stub_provider", True)
    monkeypatch.setattr(settings, "app_env", "test")

    outcomes = await cs.run("gemini", today=TODAY)

    assert len(outcomes) == len(GOOD)
    assert all(not error for _s, _r, _f, error in outcomes)
    assert "Coach scenarios:" in cs.render(outcomes)


def test_language_detection_on_chat_lines():
    assert (
        cs.language(
            "Ich habe den Freitag auf einen Ruhetag gestellt, damit du frisch bist."
        )
        == "de"
    )
    assert (
        cs.language(
            "I hear you on those shorter evenings and the juggle with interviews."
        )
        == "en"
    )


@pytest.mark.asyncio
async def test_the_trail_scenario_sends_its_objective_where_production_does(
    monkeypatch,
):
    seen = {}

    async def ask_trainer(question, plan, profile, **kwargs):
        seen.update(kwargs)
        return {"response": "Nimm das MTB.", "plan_updates": []}

    monkeypatch.setattr(cs.ai_service, "ask_trainer", ask_trainer)

    await cs.run("gemini", only="trail-motivation", today=TODAY)

    assert "trails" in seen["motivation_model"]["primary_objective"]
