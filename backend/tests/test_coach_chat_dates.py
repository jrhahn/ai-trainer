"""Dates in the coach's prompts: today, the weekday of each plan day, and history kept apart from what is coming.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations

import json
import datetime

import pytest

import services.ai_service as ai_service


def test_ask_trainer_system_includes_authoritative_date_rules():
    from services.prompts import ask_trainer_system

    prompt = ask_trainer_system(
        profile={},
        today="2026-05-26",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
        date_context=(
            "Current local date context (Europe/Berlin):\n"
            "- Today is Tuesday, May 26, 2026 (2026-05-26).\n"
            "- Yesterday was Monday, May 25, 2026 (2026-05-25).\n"
            "- Tomorrow is Wednesday, May 27, 2026 (2026-05-27)."
        ),
    )

    assert "Today is Tuesday, May 26, 2026 (2026-05-26)." in prompt
    assert "Tomorrow is Wednesday, May 27, 2026 (2026-05-27)." in prompt
    # Named rather than positional since #514 moved the rules ahead of the data:
    # "above" would now point at nothing. The snapshot holds the sentence; this
    # guards that the rule names the section.
    assert "Current local date context section" in prompt
    assert "copy it from that date context" in prompt
    assert "copy the plan entry's weekday/dateLabel fields" in prompt


@pytest.mark.asyncio
async def test_ask_trainer_passes_precomputed_date_context(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today_iso", lambda timezone_name=None: "2026-05-26"
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Tuesday, May 26, 2026 (2026-05-26).\n"
            "- Yesterday was Monday, May 25, 2026 (2026-05-25).\n"
            "- Tomorrow is Wednesday, May 27, 2026 (2026-05-27)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Use today's easy ride.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "What should I do today?",
        plan=[],
        profile={},
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"])
    assert "Today is Tuesday, May 26, 2026 (2026-05-26)." in system_prompt
    assert "Tomorrow is Wednesday, May 27, 2026 (2026-05-27)." in system_prompt


@pytest.mark.asyncio
async def test_ask_trainer_prompt_labels_upcoming_plan_weekdays(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 14)
    )
    monkeypatch.setattr(
        ai_service, "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Sunday, June 14, 2026 (2026-06-14).\n"
            "- Yesterday was Saturday, June 13, 2026 (2026-06-13).\n"
            "- Tomorrow is Monday, June 15, 2026 (2026-06-15)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Rest today and tomorrow.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "Do I rest for the next two days?",
        plan=[
            {
                "date": "2026-06-14",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-15",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
        ],
        profile={},
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"])
    assert '"date": "2026-06-14"' in system_prompt
    assert '"weekday": "Sunday"' in system_prompt
    assert '"dateLabel": "Sunday, June 14, 2026"' in system_prompt
    assert '"relativeDay": "today"' in system_prompt
    assert '"date": "2026-06-15"' in system_prompt
    assert '"weekday": "Monday"' in system_prompt
    assert '"dateLabel": "Monday, June 15, 2026"' in system_prompt
    assert '"relativeDay": "tomorrow"' in system_prompt
    assert "copy the plan entry's weekday/dateLabel fields" in system_prompt
    assert "check every weekday/date pair" in system_prompt


@pytest.mark.asyncio
async def test_ask_trainer_upcoming_days_start_today_not_past_history(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 15)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Monday, June 15, 2026 (2026-06-15).\n"
            "- Yesterday was Sunday, June 14, 2026 (2026-06-14).\n"
            "- Tomorrow is Tuesday, June 16, 2026 (2026-06-16)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Today is rest; tomorrow is recovery.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "so upcoming days are pure resting, right?",
        plan=[
            {
                "date": "2026-06-14",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-15",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-16",
                "workoutType": "recovery",
                "title": "Easy Recovery Spin",
                "durationMinutes": 60,
            },
        ],
        profile={},
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"])
    assert "Last 7 days of training (historical context, not upcoming)" in system_prompt
    assert "Upcoming plan (today and future only" in system_prompt
    assert "as TODAY and future dates only" in system_prompt
    assert "use exactly the first N entries from Upcoming plan" in system_prompt
    upcoming_section = system_prompt.split("Upcoming plan (today and future only", 1)[1]
    assert '"date": "2026-06-15"' in upcoming_section
    assert '"weekday": "Monday"' in upcoming_section
    assert '"date": "2026-06-16"' in upcoming_section
    assert '"weekday": "Tuesday"' in upcoming_section
    assert '"date": "2026-06-14"' not in upcoming_section


@pytest.mark.asyncio
async def test_ask_trainer_prompt_anchors_june_17_berlin_recent_and_upcoming(
    monkeypatch,
):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 17)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Wednesday, June 17, 2026 (2026-06-17).\n"
            "- Yesterday was Tuesday, June 16, 2026 (2026-06-16).\n"
            "- Tomorrow is Thursday, June 18, 2026 (2026-06-18)."
        ),
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        return json.dumps({"response": "Tomorrow is your endurance ride.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "What does tomorrow look like after yesterday's activity?",
        plan=[
            {
                "date": "2026-06-16",
                "workoutType": "strength",
                "title": "Krafttraining",
                "durationMinutes": 45,
            },
            {
                "date": "2026-06-17",
                "workoutType": "recovery",
                "title": "Easy Recovery Spin",
                "durationMinutes": 45,
            },
            {
                "date": "2026-06-18",
                "workoutType": "endurance",
                "title": "Aerobic Endurance",
                "durationMinutes": 90,
            },
        ],
        profile={},
        metrics_history_section=(
            "Recent activity history (newest first, historical context only):\n"
            "- 2026-06-16 Tuesday: Krafttraining, 45 min"
        ),
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"])
    assert "Today is Wednesday, June 17, 2026 (2026-06-17)." in system_prompt
    assert "Yesterday was Tuesday, June 16, 2026 (2026-06-16)." in system_prompt
    assert "Tomorrow is Thursday, June 18, 2026 (2026-06-18)." in system_prompt
    assert "- 2026-06-16 Tuesday: Krafttraining, 45 min" in system_prompt
    assert "Last 7 days of training (historical context, not upcoming)" in system_prompt
    assert "Upcoming plan (today and future only" in system_prompt
    assert "copy the plan entry's weekday/dateLabel fields" in system_prompt
    upcoming_section = system_prompt.split("Upcoming plan (today and future only", 1)[1]
    assert '"date": "2026-06-17"' in upcoming_section
    assert '"weekday": "Wednesday"' in upcoming_section
    assert '"relativeDay": "today"' in upcoming_section
    assert '"date": "2026-06-18"' in upcoming_section
    assert '"weekday": "Thursday"' in upcoming_section
    assert '"relativeDay": "tomorrow"' in upcoming_section


@pytest.mark.asyncio
async def test_today_not_duplicated_in_history_and_upcoming(monkeypatch):
    """Today's plan entry must appear only in the upcoming section, not in history.

    With ``<= today`` for the history slice, today landed in both the
    "Last 7 days" and "Upcoming plan" sections.  The model then saw today
    framed as historical context AND as an upcoming event — causing it to
    confuse which day was today vs. tomorrow.
    """
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 23)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Tuesday, June 23, 2026 (2026-06-23).\n"
            "- Yesterday was Monday, June 22, 2026 (2026-06-22).\n"
            "- Tomorrow is Wednesday, June 24, 2026 (2026-06-24)."
        ),
    )

    async def fake_chat(
        provider, system_prompt, messages, json_mode=False, task="coach", response_schema=None
    ):
        captured["system_prompt"] = system_prompt
        return json.dumps({"response": "ok", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat)

    await ai_service.ask_trainer(
        "What's today?",
        plan=[
            {"date": "2026-06-22", "workoutType": "strength", "title": "Upper Body", "durationMinutes": 45},
            {"date": "2026-06-23", "workoutType": "rest", "title": "Rest Day", "durationMinutes": 0},
            {"date": "2026-06-24", "workoutType": "vo2max", "title": "VO2 Max Intervals", "durationMinutes": 60},
        ],
        profile={},
        timezone_name="Europe/Berlin",
    )

    prompt = str(captured["system_prompt"])
    history_section, upcoming_section = prompt.split("Upcoming plan (today and future only", 1)

    # Today must be in the upcoming section
    assert '"date": "2026-06-23"' in upcoming_section
    assert '"relativeDay": "today"' in upcoming_section

    # Today must NOT appear in the history section (would create ambiguous dual framing)
    assert '"date": "2026-06-23"' not in history_section

    # Yesterday must be in history, not upcoming
    assert '"date": "2026-06-22"' in history_section
    assert '"date": "2026-06-22"' not in upcoming_section


@pytest.mark.asyncio
async def test_ask_trainer_user_message_prefixed_with_date_stamp(monkeypatch):
    """ask_trainer() must prepend a date stamp to the user message.

    The date_context block is in the system prompt, far from the actual question
    when conversation history is long.  An earlier turn that stated a wrong weekday
    sits closer to the generation point than the system prompt.  Prepending the
    stamp to the user message keeps an authoritative date immediately adjacent to
    the question, regardless of how many history turns come before it.
    """
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 23)
    )
    monkeypatch.setattr(
        ai_service, "app_today_stamp",
        lambda timezone_name=None: "[Tuesday, June 23, 2026 · 2026-06-23 · Europe/Berlin]",
    )
    monkeypatch.setattr(
        ai_service, "app_date_context",
        lambda timezone_name=None: "Current local date context (Europe/Berlin):\n- Today is Tuesday, June 23, 2026 (2026-06-23).",
    )

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, task="coach", response_schema=None
    ):
        captured["messages"] = messages
        return json.dumps({"response": "ok", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "What should I do today?",
        plan=[],
        profile={},
        conversation_history=[
            # Simulate a prior turn that stated a wrong weekday
            {"role": "assistant", "content": "Tomorrow, Wednesday June 24, is your rest day."},
        ],
        timezone_name="Europe/Berlin",
    )

    messages = captured["messages"]
    last_user_msg = messages[-1]["content"]

    # Stamp must come first
    assert last_user_msg.startswith("[Tuesday, June 23, 2026 · 2026-06-23 · Europe/Berlin]")
    # Original question preserved after the stamp
    assert "What should I do today?" in last_user_msg
    # Prior wrong assistant turn is in history but the stamp is closer to generation
    assert any("wrong" in m.get("content", "") or "Wednesday" in m.get("content", "") for m in messages[:-1])


@pytest.mark.asyncio
async def test_race_event_feedback_plan_entries_include_relative_day(monkeypatch):
    """race_event_feedback() must pass today_date to _slim_plan_entry so that
    upcoming plan entries carry relativeDay ('today', 'tomorrow') context.

    Without today_date the _slim_plan_entry call skips the relativeDay block and
    the model has no proximity cue — it only sees ISO dates and has to infer
    "tomorrow" by computing the day-of-week itself, which is an error source.
    """
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 23)
    )
    monkeypatch.setattr(
        ai_service, "app_today_iso", lambda timezone_name=None: "2026-06-23"
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, task="coach"):
        captured["user_msg"] = user_msg
        return json.dumps({"feedback": "Looks good."})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    await ai_service.race_event_feedback(
        event={"date": "2026-08-10", "distanceKm": 100},
        plan=[
            {"date": "2026-06-23", "workoutType": "rest", "title": "Rest", "durationMinutes": 0},
            {"date": "2026-06-24", "workoutType": "vo2max", "title": "VO2 Max Intervals", "durationMinutes": 60},
            {"date": "2026-06-25", "workoutType": "endurance", "title": "Long Ride", "durationMinutes": 120},
        ],
        profile={},
        timezone_name="Europe/Berlin",
    )

    user_msg = str(captured["user_msg"])
    # Today's entry must carry relativeDay so the model knows it's today
    assert '"relativeDay": "today"' in user_msg
    # Tomorrow's entry must carry relativeDay
    assert '"relativeDay": "tomorrow"' in user_msg
    # Weekday labels must be present
    assert '"weekday": "Monday"' in user_msg or '"weekday": "Tuesday"' in user_msg


@pytest.mark.asyncio
async def test_race_event_feedback_prompt_has_no_email_or_surname(monkeypatch):
    """ops#125: this prompt dumped the raw profile — email and surname must be gone.

    The one profile dump that bypasses prompt_profile, so it needs its own check:
    reverting the strip at the dump site would leave the prompt_profile tests
    green and only fail here.
    """
    captured: dict[str, object] = {}

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, task="coach"):
        captured["user_msg"] = user_msg
        return json.dumps({"feedback": "Looks good."})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    await ai_service.race_event_feedback(
        event={"date": "2027-08-10", "distanceKm": 100},
        plan=[],
        profile={
            "name": "John Doe",
            "email": "john.doe@example.com",
            "trainingGoal": "race",
        },
        timezone_name="Europe/Berlin",
    )

    user_msg = str(captured["user_msg"])
    assert "john.doe@example.com" not in user_msg
    assert "Doe" not in user_msg
    assert "John" in user_msg  # first name kept for the greeting
