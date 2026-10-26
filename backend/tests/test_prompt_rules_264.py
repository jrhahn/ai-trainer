"""Contradicting and stale prompt rules (ai-trainer-ops#264).

The chat asked how every unnoted ride felt while its curiosity rules banned
that question; the plan writers held "two hard days back-to-back" at three
strengths and the taper at three numbers; and the prompts spoke of Strava to
testers who sync from intervals.icu, and of a settings page that does not exist.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from services import prompts, untrusted_text
from tests.prompt_fixtures import BUILDERS


@pytest.mark.parametrize(
    "system", [prompts.adapt_plan_system, prompts.generate_plan_system]
)
def test_the_plan_writers_state_back_to_back_once_as_the_heuristic_it_is(system):
    text = system().lower()

    assert text.count("back-to-back") == 1
    assert "never schedule two hard days" not in text
    assert "back-to-back hard days (heuristic)" in text


def test_the_taper_has_its_numbers_once():
    principles = prompts.TRAINING_PLAN_PRINCIPLES
    alert = prompts.adapt_plan_user({}, "2026-10-26", [], [], taper_days_remaining=6)

    assert "40-60 %" in principles and "~40%" not in principles
    assert "TAPER ALERT" in alert
    assert "~40%" not in alert and "TSB +5" not in alert


def test_the_chat_asks_one_specific_question_about_an_unnoted_ride_not_how_it_felt():
    prompt = BUILDERS["ask_trainer_minimal"]()

    assert "proactively ask the athlete how it felt" not in prompt
    # The cue is named: the lines the ride section writes for a note and a leg feel.
    assert "neither an 'Athlete:' note nor an 'Athlete legs:' line" in prompt


def test_an_unnoted_ride_is_not_marked_for_the_generic_question():
    ride = SimpleNamespace(
        activity_date="2026-10-25",
        sport_type="cycling",
        ride_purpose="endurance",
        duration_seconds=3600,
        tss=60,
        user_note=None,
    )

    section = prompts.ride_metrics_context_section([ride], today=date(2026, 10, 26))
    noted = prompts.ride_metrics_context_section(
        [
            SimpleNamespace(
                **{**vars(ride), "user_note": "Wind von vorn", "feel_legs": "heavy"}
            )
        ],
        today=date(2026, 10, 26),
    )

    assert "consider asking how this ride felt" not in section
    assert "Athlete: «Wind von vorn»" in noted and "Athlete legs: heavy" in noted


def test_no_fixed_prompt_names_strava_but_the_list_of_data_sources():
    named = [
        name
        for name, build in BUILDERS.items()
        if "Strava" in build().replace(untrusted_text.DATA_NOT_INSTRUCTION_RULE, "")
    ]

    assert named == []
    rides = prompts.analyse_activities_user(
        [{"id": 1, "name": "Ride", "type": "Ride", "moving_time": 3600}],
        "",
        "",
        sport_type="cycling",
    )
    assert "synced rides" in rides and "Strava" not in rides


@pytest.mark.asyncio
async def test_the_plan_writer_names_no_strava_in_its_assessment(monkeypatch):
    """The plan writer's own line, outside prompts.py (ai_service.generate_training_plan)."""
    import json

    from services import ai_service

    seen: list[str] = []

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        seen.append(user_msg)
        return json.dumps({"plan": []})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    try:
        await ai_service.generate_training_plan(
            {"name": "Alice"}, rider_assessment={"estimatedFTP": 250}
        )
    except Exception:
        pass  # an empty plan may be refused; the prompt is what is checked

    assert (
        seen
        and "Rider assessment from recent activities" in seen[0]
        and "Strava" not in seen[0]
    )


def test_the_settings_hint_example_names_a_page_that_exists():
    prompt = BUILDERS["generate_inquiries"]()

    assert "e.g. 'Settings'" in prompt and "Settings > Athlete" not in prompt
