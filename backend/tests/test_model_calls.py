"""Model calls end in time, are not asked twice when refused, and are not shown cut off (ai-trainer-ops#120)."""

import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types

from services import ai_service, embeddings, llm, plan_maintenance
from tests.conftest import TestSessionLocal
from tests.test_plan_maintenance import _create_user_with_plan, _day


@pytest.fixture
def short_timeouts(monkeypatch):
    for task in list(llm.TASK_TIMEOUT_SECONDS):
        monkeypatch.setitem(llm.TASK_TIMEOUT_SECONDS, task, 0.05)


@pytest.fixture
def recorded(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(llm.token_accounting, "record_call", lambda **kw: calls.append(kw))
    return calls


async def _stall(*args, **kwargs):
    # Far past every shortened timeout, short enough that a missing one fails
    # the test rather than hanging the suite.
    await asyncio.sleep(10)


def _gemini(respond):
    """A fake ``genai.Client`` whose ``generate_content`` awaits ``respond()``."""
    seen: dict[str, list] = {"configs": [], "http_options": []}

    class _Models:
        async def generate_content(self, *, model, contents, config):
            seen["configs"].append(config)
            return await respond()

    class _Aio:
        models = _Models()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, api_key=None, http_options=None):
            seen["http_options"].append(http_options)
            self.aio = _Aio()

    return _Client, seen


def _answer(text: str | None, finish: types.FinishReason = types.FinishReason.STOP):
    async def respond():
        parts = [types.Part(text=text)] if text is not None else []
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)
            ]
        )

    return respond


async def _blocked_prompt():
    return types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        )
    )


def _openai(finish_reason: str, content: str = "partial"):
    provider = llm.OpenAIProvider.__new__(llm.OpenAIProvider)
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    completion = MagicMock()
    completion.choices = [choice]
    provider._client = MagicMock()
    provider._client.chat.completions.create = AsyncMock(return_value=completion)
    return provider


# --- A stalled provider ends in time --------------------------------------------


@pytest.mark.asyncio
async def test_a_stalled_gemini_call_ends_as_a_provider_error_in_time(short_timeouts, recorded):
    client, _seen = _gemini(_stall)
    started = time.perf_counter()

    with patch("google.genai.Client", client), pytest.raises(llm.AIProviderError) as raised:
        await llm.GeminiProvider(api_key="k", task=llm.TASK_COACH).chat("system", "user")

    assert time.perf_counter() - started < 2
    assert "did not answer" in str(raised.value)
    assert recorded[-1]["ok"] is False and recorded[-1]["error"] == "TimeoutError"


@pytest.mark.asyncio
async def test_a_stalled_openai_call_ends_as_a_provider_error_in_time(short_timeouts, recorded):
    provider = _openai("stop")
    provider._client.chat.completions.create = AsyncMock(side_effect=_stall)

    with pytest.raises(llm.AIProviderError):
        await provider.chat("system", "user")

    assert recorded[-1]["error"] == "TimeoutError"


@pytest.mark.asyncio
async def test_a_stalled_call_does_not_hold_up_the_nightly_job(short_timeouts, monkeypatch):
    """One athlete's stalled call stopped the loop over every athlete until a restart."""
    for email in ("stalled@example.com", "next@example.com"):
        await _create_user_with_plan(email=email, plan=[_day("2026-06-08"), _day("2026-06-10")])
    calls: list[list[dict]] = []

    async def adapt_training_plan(plan, *args, **kwargs):
        calls.append(plan)
        if len(calls) == 1:
            return await llm.GeminiProvider(api_key="k", task=llm.TASK_PLAN).chat("s", "u")
        return [
            {**plan[0], "date": "2026-06-09"},
            {**plan[0], "workoutType": "rest", "title": "Rest", "durationMinutes": 0},
        ]

    async def no_weather(*args, **kwargs):
        return ""

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", adapt_training_plan)
    monkeypatch.setattr(plan_maintenance, "training_weather_context_for_user", no_weather)
    client, _seen = _gemini(_stall)
    started = time.perf_counter()

    with patch("google.genai.Client", client):
        result = await plan_maintenance.run_daily_plan_maintenance(
            TestSessionLocal,
            now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
            timezone_name="UTC",
        )

    assert time.perf_counter() - started < 5
    assert (result.checked, result.failed, result.updated) == (2, 1, 1)


def test_each_task_has_a_timeout_and_an_output_cap_inside_every_models_limit():
    for task in (llm.TASK_CLASSIFY, llm.TASK_COACH, llm.TASK_FEEDBACK, llm.TASK_PLAN):
        assert 0 < llm.task_timeout(task) <= 150
        assert 0 < llm.task_max_output_tokens(task) <= 8192
    # nginx waits longer than the longest call.
    conf = (
        __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"
    ).read_text()
    assert "proxy_read_timeout 300s;" in conf
    assert 300 > max(llm.TASK_TIMEOUT_SECONDS.values())


@pytest.mark.asyncio
async def test_the_gemini_client_gets_the_tasks_timeout_one_retry_and_the_cap():
    client, seen = _gemini(_answer("Fine."))

    with patch("google.genai.Client", client):
        assert await llm.GeminiProvider(api_key="k", task=llm.TASK_PLAN).chat("s", "u") == "Fine."

    options = seen["http_options"][-1]
    assert options.timeout == 150_000
    assert options.retry_options.attempts == 2
    # A 429 is the athlete's or the owner's quota, not a blip to retry.
    assert 429 not in options.retry_options.http_status_codes
    assert seen["configs"][-1].max_output_tokens == llm.TASK_MAX_OUTPUT_TOKENS[llm.TASK_PLAN]


@pytest.mark.asyncio
async def test_the_openai_call_gets_the_tasks_cap():
    provider = _openai("stop", "Fine.")

    assert await provider.chat("s", "u") == "Fine."

    kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert kwargs["max_completion_tokens"] == llm.TASK_MAX_OUTPUT_TOKENS[llm.TASK_COACH]


def test_the_openai_client_gets_the_tasks_timeout_and_one_retry():
    provider = llm.OpenAIProvider(api_key="k", task=llm.TASK_FEEDBACK)

    assert provider._client.timeout == llm.TASK_TIMEOUT_SECONDS[llm.TASK_FEEDBACK]
    assert provider._client.max_retries == 1


# --- A refused reply is not asked again, a cut-off one is not shown ---------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "respond",
    [_answer(None, types.FinishReason.SAFETY), _answer("Half a", types.FinishReason.SAFETY), _blocked_prompt],
)
async def test_a_gemini_block_is_a_content_error(respond):
    client, _seen = _gemini(respond)

    with patch("google.genai.Client", client), pytest.raises(llm.AIContentBlockedError) as raised:
        await llm.GeminiProvider(api_key="k").chat("s", "u")

    assert raised.value.key_refused is False
    assert raised.value.reason == "SAFETY"
    assert "declined" in str(raised.value)


@pytest.mark.asyncio
async def test_a_blocked_coach_reply_is_asked_once():
    """An empty reply is retried; a safety block came back empty and was asked three times."""
    client, seen = _gemini(_answer(None, types.FinishReason.SAFETY))
    provider = llm.GeminiProvider(api_key="k", task=llm.TASK_COACH)

    async def chat_history(_provider, system_prompt, messages, json_mode=False, **kwargs):
        return await provider.chat_history(
            system_prompt, messages, json_mode, kwargs.get("response_schema")
        )

    with patch("google.genai.Client", client), patch.object(
        ai_service, "_chat_history", side_effect=chat_history
    ), pytest.raises(llm.AIContentBlockedError):
        await ai_service.ask_trainer("Hallo", [], {"fitnessLevel": "intermediate"})

    assert len(seen["configs"]) == 1


@pytest.mark.asyncio
async def test_a_gemini_reply_cut_off_at_its_limit_is_a_format_error():
    client, _seen = _gemini(_answer('{"response": "Ride ste', types.FinishReason.MAX_TOKENS))

    with patch("google.genai.Client", client), pytest.raises(ai_service.AIResponseFormatError):
        await llm.GeminiProvider(api_key="k").chat("s", "u")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "error"),
    [("length", ai_service.AIResponseFormatError), ("content_filter", llm.AIContentBlockedError)],
)
async def test_an_openai_reply_that_did_not_finish_is_an_error(finish_reason, error):
    with pytest.raises(error):
        await _openai(finish_reason).chat("s", "u")


@pytest.mark.asyncio
async def test_a_plan_cut_off_is_asked_for_again():
    """Repaired, a cut-off plan was saved as a shorter one."""
    start = date(2026, 6, 1)
    fortnight = [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "workoutType": "endurance",
            "title": "Ride",
            "description": "Steady",
            "durationMinutes": 60,
        }
        for offset in range(14)
    ]
    sent: list[str] = []

    async def chat(provider, system_prompt, user_msg, json_mode=False, task=None):
        sent.append(user_msg)
        if len(sent) == 1:
            raise ai_service.AIResponseFormatError("The gemini reply was cut off at its output limit")
        return json.dumps({"plan": fortnight})

    with patch.object(ai_service, "_chat", side_effect=chat):
        plan = await ai_service._generate_plan_days("gemini", "s", "u", today=start.isoformat())

    assert len(plan) == 14
    assert "cut off" in sent[1]


# --- Embeddings end in time too -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_stalled_embedding_ends_as_an_embedding_error(monkeypatch):
    monkeypatch.setattr(embeddings, "EMBED_TIMEOUT_SECONDS", 0.05)

    class _Models:
        async def embed_content(self, **kwargs):
            await _stall()

    class _Aio:
        models = _Models()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, api_key=None):
            self.aio = _Aio()

    with patch("google.genai.Client", _Client), pytest.raises(embeddings.EmbeddingError):
        await embeddings.GeminiEmbedder(api_key="k").embed(["text"], task_type="RETRIEVAL_QUERY")


@pytest.mark.asyncio
async def test_a_stalled_openai_embedding_ends_as_an_embedding_error(monkeypatch):
    monkeypatch.setattr(embeddings, "EMBED_TIMEOUT_SECONDS", 0.05)
    client = MagicMock()
    client.embeddings.create = AsyncMock(side_effect=_stall)

    with patch("openai.AsyncOpenAI", return_value=client), pytest.raises(embeddings.EmbeddingError):
        await embeddings.OpenAIEmbedder(api_key="k").embed(["text"], task_type="RETRIEVAL_QUERY")
