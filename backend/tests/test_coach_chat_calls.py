"""ask_trainer with a mocked model: what it returns, how it retries, and what reaches the prompt.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations

import json
import datetime
from unittest.mock import AsyncMock, patch

import pytest

import services.ai_service as ai_service


# ---------------------------------------------------------------------------
# ask_trainer — thinking stripped, classification mock (Tasks 2 & 5)
# ---------------------------------------------------------------------------

PLAN_FOR_LOAD_TESTS = [
    {
        "date": "2026-04-16",
        "workoutType": "intervals",
        "title": "VO2 Intervals",
        "description": "5×4 min at 110% FTP",
        "durationMinutes": 60,
        "targetPower": {"low": 280, "high": 320},
    }
]


PROFILE_WITH_FTP = {
    "name": "Test Rider",
    "email": "rider@example.com",
    "bikeType": "road",
    "trainingGoal": "general_fitness",
    "fitnessLevel": "intermediate",
    "currentFTP": 300,
}


@pytest.mark.asyncio
async def test_ask_trainer_thinking_not_in_return():
    """'thinking' must be stripped from the return value and never reach the frontend."""

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        return json.dumps(
            {
                "thinking": "The athlete is asking about tomorrow's workout...",
                "response": "Tomorrow is an endurance ride.",
                "planUpdates": [],
                "sources": [],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="What's tomorrow's workout?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert "thinking" not in result
    assert result["response"] == "Tomorrow is an endurance ride."


@pytest.mark.asyncio
async def test_ask_trainer_classify_step_skips_rag_when_not_needed():
    """When classify says needs_science_rag=False, the effective science context is empty."""
    captured_system_prompt: list[str] = []

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        captured_system_prompt.append(system_prompt)
        return json.dumps(
            {"response": "Here's your plan.", "planUpdates": [], "sources": []}
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="What's tomorrow's workout?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
            # No science_context provided; should not be in the prompt
        )

    assert result["response"] == "Here's your plan."
    # No science research section should appear in the prompt
    assert "Relevant cycling science research" not in captured_system_prompt[0]


@pytest.mark.asyncio
async def test_ask_trainer_training_load_in_prompt():
    """CTL/ATL/TSB should appear in the system prompt when FTP is known."""
    captured_prompt: list[str] = []

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        captured_prompt.append(system_prompt)
        return json.dumps({"response": "OK.", "planUpdates": [], "sources": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        await ai_service.ask_trainer(
            question="Am I too tired?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    prompt = captured_prompt[0]
    assert "CTL" in prompt
    assert "ATL" in prompt
    assert "TSB" in prompt


# ---------------------------------------------------------------------------
# ask_trainer — a reply that ignores the JSON contract (#558)
#
# Production, three requests in a row, same question: the model answered in
# German prose, repair_json emptied it, json.loads raised, and the router — which
# has no handler for JSONDecodeError — returned 500 with a traceback. The
# athlete's question is not persisted on a failed request, so the turn was gone.
# ---------------------------------------------------------------------------


PROSE_REPLY = (
    "Das klingt gut! Sonntag 2-3 Stunden Grundlage passt gut ins Wochenende, "
    "solange du Samstag nicht überziehst."
)


@pytest.mark.asyncio
async def test_ask_trainer_returns_a_prose_reply_as_the_answer():
    calls: list[str] = []

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        calls.append(kwargs.get("task", ""))
        return PROSE_REPLY

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="vllt gehn sonntag ja noch 2-3h grundlage?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["response"] == PROSE_REPLY
    # Prose carries no structure, so there is nothing to write to the plan.
    assert not result["plan_updates"]
    assert result["sources"] == []
    # And it costs exactly one call: the same prompt produced prose three times
    # out of three in production, so retrying only buys another 17k input tokens.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ask_trainer_logs_the_prose_reply_by_shape_and_never_by_content(caplog):
    """The reply is the athlete's health data — sizes may be logged, text may not (#499)."""
    import logging

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        return PROSE_REPLY

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        with caplog.at_level(logging.WARNING, logger="services.ai_service"):
            await ai_service.ask_trainer(
                question="und sonntag?",
                plan=PLAN_FOR_LOAD_TESTS,
                profile=PROFILE_WITH_FTP,
            )

    line = next(
        r.getMessage()
        for r in caplog.records
        if "outside the JSON contract" in r.getMessage()
    )
    assert f"chars={len(PROSE_REPLY)}" in line
    assert "Grundlage" not in line
    assert "Sonntag" not in line


@pytest.mark.asyncio
async def test_ask_trainer_raises_the_format_error_when_the_reply_holds_no_answer():
    """Structure-less *and* wordless: that is the retry path, ending in a 502.

    ``AIResponseFormatError`` is what ``routers/ai.py`` already turns into a 502
    with an explanation; a bare ``JSONDecodeError`` is what it 500s on.
    """

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        return "{{{"

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        with patch.object(ai_service.asyncio, "sleep", new=AsyncMock()):
            with pytest.raises(ai_service.AIResponseFormatError):
                await ai_service.ask_trainer(
                    question="und sonntag?",
                    plan=PLAN_FOR_LOAD_TESTS,
                    profile=PROFILE_WITH_FTP,
                )


@pytest.mark.asyncio
async def test_ask_trainer_still_reads_plan_updates_out_of_a_valid_reply():
    """The fallback must not swallow the structured path it sits next to."""

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        return json.dumps(
            {
                "response": "Samstag 4 Stunden, danach Pause.",
                "planUpdates": [{"date": "2026-04-16", "durationMinutes": 240}],
                "sources": ["a"],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="samstag lang?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["plan_updates"] == [{"date": "2026-04-16", "durationMinutes": 240}]
    assert result["sources"] == ["a"]
    # The unit stripper used to turn this into "Samstag 4, danach Pause." (#558).
    assert result["response"] == "Samstag 4 Stunden, danach Pause."


# ---------------------------------------------------------------------------
# classify_question (Task 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_question_returns_default_on_failure():
    """classify_question must return a safe default if the AI call fails."""

    async def broken_chat(provider, system_prompt, user_msg, json_mode=False):
        raise RuntimeError("API unavailable")

    with patch.object(ai_service, "_chat", side_effect=broken_chat):
        result = await ai_service.classify_question("What's my FTP?")

    assert result["category"] == "general_coaching"
    assert result["needs_science_rag"] is False


@pytest.mark.asyncio
async def test_classify_question_parses_response():
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        return json.dumps({"category": "science_question", "needs_science_rag": True})

    with patch.object(ai_service, "_chat", side_effect=fake_chat):
        result = await ai_service.classify_question("How does VO2max training work?")

    assert result["category"] == "science_question"
    assert result["needs_science_rag"] is True


@pytest.mark.asyncio
async def test_ask_trainer_outlook_prompt_contains_outlook_rules():
    """When ask_trainer is called, the system prompt passed to the LLM contains outlook rules."""
    captured_prompt: list[str] = []

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        captured_prompt.append(system_prompt)
        return json.dumps({"response": "Here are your next sessions.", "sources": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        await ai_service.ask_trainer(
            question="Show me an outlook for my next few sessions",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert len(captured_prompt) >= 1
    assert "outlook" in captured_prompt[0].lower()
    assert "3-5" in captured_prompt[0]


@pytest.mark.asyncio
async def test_ask_trainer_outlook_no_plan_updates_when_ai_omits_them():
    """Asking for an outlook that does not include planUpdates should return no plan updates."""

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        # AI returns an outlook response with no planUpdates
        return json.dumps(
            {
                "response": "Next up: endurance on Wed, intervals Thu, long ride Sat.",
                "sources": [],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="Show me an outlook for my next few sessions",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    # plan_updates must be absent / empty when the AI does not return them
    assert result.get("plan_updates") is None or result.get("plan_updates") == []
    assert (
        "endurance" in result["response"].lower()
        or "next" in result["response"].lower()
    )


@pytest.mark.asyncio
async def test_ask_trainer_returns_separate_physiology_and_context_rationale():
    """The two reasoning layers are surfaced as separate structured fields."""

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        return json.dumps(
            {
                "response": "On the numbers an easy spin is fine, but knowing you, rest today.",
                "physiologyRationale": "TSB neutral, an easy Z2 ride would be tolerable",
                "contextRationale": "tends to turn easy rides hard, so full rest protects recovery",
                "sources": [],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="Can I do an easy ride today?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["physiology_rationale"] == (
        "TSB neutral, an easy Z2 ride would be tolerable"
    )
    assert result["context_rationale"] == (
        "tends to turn easy rides hard, so full rest protects recovery"
    )


@pytest.mark.asyncio
async def test_ask_trainer_personal_context_overrides_metrics_recommendation():
    """Personal context can override a metrics-only recommendation."""

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        # Physiology alone would permit a ride; context drives the rest decision.
        return json.dumps(
            {
                "response": "The numbers say a Z2 ride is okay, but knowing you I'd rest.",
                "physiologyRationale": "fresh enough for an easy ride",
                "contextRationale": "history of overreaching means rest is the safer call",
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="I feel restless, can I squeeze in a ride?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    # The physiology layer permits a ride, but the context layer wins.
    assert "fresh" in result["physiology_rationale"]
    assert "rest" in result["context_rationale"]
    assert "rest" in result["response"].lower()


@pytest.mark.asyncio
async def test_ask_trainer_omits_blank_rationale_fields():
    """Blank rationale fields normalise to None rather than empty strings."""

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        return json.dumps(
            {
                "response": "Easy endurance today keeps the base building.",
                "physiologyRationale": "   ",
                "sources": [],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question="What should I do today?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["physiology_rationale"] is None
    assert result["context_rationale"] is None


@pytest.mark.asyncio
async def test_ask_trainer_rejects_empty_response_after_retries():
    call_count = 0

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        nonlocal call_count
        call_count += 1
        return json.dumps({"response": "   ", "sources": []})

    with (
        patch.object(ai_service, "_chat_history", side_effect=fake_chat_history),
        patch.object(ai_service.asyncio, "sleep", new=AsyncMock()),
    ):
        with pytest.raises(ai_service.AIResponseFormatError):
            await ai_service.ask_trainer(
                question="Why did the coach not answer?",
                plan=PLAN_FOR_LOAD_TESTS,
                profile=PROFILE_WITH_FTP,
            )

    # The first attempt and one retry: each resends the whole prompt
    # (ai-trainer-ops#338).
    assert call_count == 2


@pytest.mark.asyncio
async def test_ask_trainer_retries_empty_response_then_succeeds():
    responses = iter(
        [
            json.dumps({"response": "   ", "sources": []}),
            json.dumps({"response": "Here is your plan.", "sources": []}),
        ]
    )

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        return next(responses)

    sleep_mock = AsyncMock()
    with (
        patch.object(ai_service, "_chat_history", side_effect=fake_chat_history),
        patch.object(ai_service.asyncio, "sleep", new=sleep_mock),
    ):
        result = await ai_service.ask_trainer(
            question="Why did the coach not answer?",
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    assert result["response"] == "Here is your plan."
    # Backed off exactly once before the successful retry.
    sleep_mock.assert_awaited_once_with(ai_service.ASK_TRAINER_RETRY_BASE_DELAY)


@pytest.mark.asyncio
async def test_ask_trainer_rest_prompt_handles_corrected_vo2_timing(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 6, 19)
    )
    monkeypatch.setattr(
        ai_service,
        "app_date_context",
        lambda timezone_name=None: (
            "Current local date context (Europe/Berlin):\n"
            "- Today is Friday, June 19, 2026 (2026-06-19).\n"
            "- Yesterday was Thursday, June 18, 2026 (2026-06-18).\n"
            "- Tomorrow is Saturday, June 20, 2026 (2026-06-20)."
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
        return json.dumps({"response": "Saturday can be easy Z2.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "I did the VO2max training yesterday because today I have no time. "
        "With CTL 56.0, ATL 53.9, TSB +2.1, TSS 135, RPE 7/10 and fresh legs, "
        "can I do a nice endurance ride tomorrow? Show the numbers.",
        plan=[
            {
                "date": "2026-06-18",
                "workoutType": "intervals",
                "title": "VO2max intervals",
                "durationMinutes": 75,
            },
            {
                "date": "2026-06-19",
                "workoutType": "rest",
                "title": "No training available",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-20",
                "workoutType": "rest",
                "title": "Complete Rest Day",
                "durationMinutes": 0,
            },
            {
                "date": "2026-06-21",
                "workoutType": "sweet_spot",
                "title": "Sweet Spot intervals",
                "durationMinutes": 90,
            },
        ],
        profile=PROFILE_WITH_FTP,
        metrics_history_section=(
            "Current ride-derived load: CTL 56.0, ATL 53.9, TSB +2.1. "
            "Yesterday: VO2max, TSS 135, RPE 7/10, legs fresh."
        ),
        timezone_name="Europe/Berlin",
    )

    system_prompt = str(captured["system_prompt"]).lower()
    assert "unavailable day with no training counts" in system_prompt
    assert "do not claim two complete rest days are mandatory" in system_prompt
    assert "neutral or positive tsb" in system_prompt
    assert "bounded easy z2/recovery ride" in system_prompt
    assert "re-evaluate from that corrected sequence" in system_prompt
    assert "show the numbers" in system_prompt
    assert "ctl 56.0, atl 53.9, tsb +2.1" in system_prompt
    assert "tss 135" in system_prompt


@pytest.mark.asyncio
async def test_ask_trainer_prompt_handles_hot_long_ride_as_high_signal():
    """A casual hot long-ride message should be sent with targeted follow-up guidance."""
    captured_prompt: list[str] = []

    async def fake_chat_history(
        provider, system_prompt, messages, json_mode=False, **kwargs
    ):
        captured_prompt.append(system_prompt)
        assert "so nach dem hike" in messages[-1]["content"]
        return json.dumps(
            {
                "response": "Wie viel und was hast du unterwegs getrunken?",
                "sources": [],
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        result = await ai_service.ask_trainer(
            question=(
                "so nach dem hike gestern bin ich heute 5h mtb gefahren. richtig geil. "
                "nur super heiß im laufe des tages. über 30 grad. trinken war dann leer"
            ),
            plan=PLAN_FOR_LOAD_TESTS,
            profile=PROFILE_WITH_FTP,
        )

    prompt = captured_prompt[0].lower()
    assert "running out of drink" in prompt
    assert "drink volume" in prompt or "drink type" in prompt
    assert "targeted follow-up" in prompt
    assert "wie viel" in result["response"].lower()


@pytest.mark.asyncio
async def test_ask_trainer_returns_ride_label_update(monkeypatch):
    """ask_trainer() must pass ride_label_update from the LLM response to the caller.

    Previously the field was silently dropped because the return dict only included
    ride_note_update but not ride_label_update, so the router's result.pop("ride_label_update")
    always got None and label corrections were never persisted or sent to the frontend.
    """

    async def fake_chat_history(
        provider,
        system_prompt,
        messages,
        json_mode=False,
        task="coach",
        response_schema=None,
    ):
        return json.dumps(
            {
                "response": "I've updated your activity label for June 24 to OK.",
                "ride_label_update": {"activity_date": "2026-06-24", "label": "OK"},
            }
        )

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    result = await ai_service.ask_trainer(
        "Please change the label for June 24 to OK.",
        plan=[],
        profile={},
    )

    assert result["ride_label_update"] == {"activity_date": "2026-06-24", "label": "OK"}


@pytest.mark.asyncio
async def test_ask_trainer_ride_label_update_is_none_when_absent(monkeypatch):
    """ride_label_update must be None (not KeyError) when the LLM omits the field."""

    async def fake_chat_history(
        provider,
        system_prompt,
        messages,
        json_mode=False,
        task="coach",
        response_schema=None,
    ):
        return json.dumps({"response": "Great ride today!"})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    result = await ai_service.ask_trainer("How did I do?", plan=[], profile={})

    assert result.get("ride_label_update") is None
