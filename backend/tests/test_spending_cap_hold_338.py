"""After a spending cap, the key is held instead of asked again
(review of 2026-11-01, architecture C7, ai-trainer-ops#338).

A cap lasts until the owner raises it or the month rolls over. Every further
call met the same 429: the nightly jobs walked on to the next athlete, and the
dashboard retried on every load.
"""

from __future__ import annotations

import pytest

from services import llm

CAP = "429 RESOURCE_EXHAUSTED: Your project has exceeded its monthly spending cap."


@pytest.fixture(autouse=True)
def _no_holds(monkeypatch):
    monkeypatch.setattr(llm, "_CAPPED_UNTIL", {})


def _counting(error: BaseException | None = None):
    calls: list[int] = []

    async def invoke():
        calls.append(1)
        if error is not None:
            raise error
        return object()

    return calls, invoke


async def _call(api_key: str, invoke):
    return await llm._observed(
        task=llm.TASK_COACH,
        provider_name="gemini",
        model="m",
        api_key=api_key,
        system="s",
        json_mode=False,
        invoke=invoke,
        extract=lambda _r: llm._CallTokens(),
    )


@pytest.mark.asyncio
async def test_a_capped_key_is_not_asked_again():
    calls, invoke = _counting(llm.AISpendingCapError(CAP))

    for _ in range(3):
        with pytest.raises(llm.AISpendingCapError):
            await _call("shared", invoke)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_another_key_is_still_asked():
    _, capped = _counting(llm.AISpendingCapError(CAP))
    with pytest.raises(llm.AISpendingCapError):
        await _call("shared", capped)

    calls, invoke = _counting()
    await _call("athletes-own", invoke)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_the_key_is_asked_again_after_the_hold(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: now[0])
    _, capped = _counting(llm.AISpendingCapError(CAP))
    with pytest.raises(llm.AISpendingCapError):
        await _call("shared", capped)

    calls, invoke = _counting()
    now[0] += llm.CAP_HOLD_SECONDS - 1
    with pytest.raises(llm.AISpendingCapError):
        await _call("shared", invoke)
    now[0] += 2
    await _call("shared", invoke)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_throttle_holds_nothing():
    calls, invoke = _counting(llm.AIRateLimitError("429 Quota exceeded for metric rpm"))

    for _ in range(2):
        with pytest.raises(llm.AIRateLimitError):
            await _call("shared", invoke)

    assert len(calls) == 2


class _Quota(Exception):
    status_code = 429


@pytest.mark.asyncio
async def test_a_cap_seen_by_its_status_code_holds_too():
    calls, invoke = _counting(_Quota("insufficient_quota"))

    for _ in range(2):
        with pytest.raises(llm.AISpendingCapError):
            await _call("shared", invoke)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_each_provider_names_its_key(monkeypatch):
    seen: list[str | None] = []

    async def observed(**kwargs):
        seen.append(kwargs["api_key"])
        raise llm.AISpendingCapError(CAP)

    monkeypatch.setattr(llm, "_observed", observed)
    for provider in (
        llm.GeminiProvider(api_key="gemini-key"),
        llm.OpenAIProvider(api_key="openai-key"),
    ):
        with pytest.raises(llm.AISpendingCapError):
            await provider.chat("s", "u")

    assert seen == ["gemini-key", "openai-key"]
