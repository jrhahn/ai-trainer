"""Tests for per-call LLM cost accounting (#516).

The point of the feature is that a log line can answer "which feature is
costing me money" and that no provider call is billed to nobody, so that is
what these assert: the shape of the line, the input/output/cached split
reaching the user's counters, and the paths that previously escaped a scope.
"""

from __future__ import annotations

import logging

import pytest

import crud
import models
from auth import hash_password
from config import settings
from services import token_accounting
from tests.conftest import TestSessionLocal


def _call(**overrides) -> None:
    kwargs = dict(
        task="coach",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        system_prompt="You are a cycling coach.",
        json_mode=False,
        latency_ms=1234,
        input_tokens=12_000,
        output_tokens=480,
        cached_tokens=9_000,
        total_tokens=12_480,
    )
    kwargs.update(overrides)
    token_accounting.record_call(**kwargs)


async def _create_user(email: str) -> str:
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email=email, name="T", hashed_password=hash_password("pw")
        )
        await db.commit()
        return user.id


# ---------------------------------------------------------------------------
# The log line
# ---------------------------------------------------------------------------


def test_a_call_logs_every_field_needed_to_price_it(caplog):
    with caplog.at_level(logging.INFO, logger="services.token_accounting"):
        _call()
    line = next(r.getMessage() for r in caplog.records if "LLM call" in r.getMessage())
    for fragment in (
        "task=coach",
        "provider=gemini",
        "model=gemini-3.5-flash-lite",
        "input=12000",
        "output=480",
        "cached=9000",
        "total=12480",
        "latency_ms=1234",
        "json_mode=false",
        "ok=true",
        "prompt_sha=",
    ):
        assert fragment in line, fragment


def test_a_call_outside_any_scope_is_still_logged_and_marked(caplog):
    """An unscoped call is billed to nobody — it must not also be invisible."""
    with caplog.at_level(logging.INFO, logger="services.token_accounting"):
        _call()
    line = next(r.getMessage() for r in caplog.records if "LLM call" in r.getMessage())
    assert f"source={token_accounting.UNSCOPED}" in line


def test_a_failed_call_is_logged_with_its_error(caplog):
    with caplog.at_level(logging.INFO, logger="services.token_accounting"):
        _call(ok=False, error="AIRateLimitError", total_tokens=0)
    line = next(r.getMessage() for r in caplog.records if "LLM call" in r.getMessage())
    assert "ok=false" in line
    assert "error=AIRateLimitError" in line


def test_the_prompt_fingerprint_changes_when_the_prompt_does():
    """A prompt edit has to be greppable, or a cost jump cannot be explained."""
    before = token_accounting.prompt_fingerprint("You are a cycling coach.")
    after = token_accounting.prompt_fingerprint("You are a cycling coach!")
    assert before != after
    assert before == token_accounting.prompt_fingerprint("You are a cycling coach.")


def test_payloads_are_not_logged_unless_explicitly_switched_on(caplog, monkeypatch):
    """Prompts carry the athlete's health data (#499)."""
    with caplog.at_level(logging.DEBUG, logger="services.token_accounting"):
        token_accounting.log_payload("system", "resting HR 41, poor sleep")
    assert not any("resting HR 41" in r.getMessage() for r in caplog.records)

    monkeypatch.setattr(settings, "log_llm_payloads", True)
    with caplog.at_level(logging.DEBUG, logger="services.token_accounting"):
        token_accounting.log_payload("system", "resting HR 41, poor sleep")
    assert any("resting HR 41" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Collection and persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_reaches_the_users_counters_split_by_how_it_bills():
    user_id = await _create_user("accounting-split@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        async with token_accounting.track_llm_usage(db, user, source="api:ask_trainer"):
            _call()
        await db.commit()

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
    assert user.consumed_tokens == 12_480
    assert user.consumed_input_tokens == 12_000
    assert user.consumed_output_tokens == 480
    # cached is a subset of input, so it must not inflate the total
    assert user.consumed_cached_tokens == 9_000


@pytest.mark.asyncio
async def test_several_calls_in_one_scope_are_summed():
    user_id = await _create_user("accounting-sum@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        async with token_accounting.track_llm_usage(db, user, source="job:x"):
            _call(input_tokens=100, output_tokens=10, cached_tokens=0, total_tokens=110)
            _call(input_tokens=200, output_tokens=20, cached_tokens=0, total_tokens=220)
        await db.commit()

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
    assert user.consumed_tokens == 330
    assert user.consumed_input_tokens == 300
    assert user.consumed_output_tokens == 30


@pytest.mark.asyncio
async def test_a_nested_scope_bills_its_calls_once():
    """The sync wrapper encloses steps that open their own scope (#516).

    The inner scope persists what it collected; the outer must not persist the
    same tokens a second time.
    """
    user_id = await _create_user("accounting-nested@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        async with token_accounting.track_llm_usage(db, user, source="job:sync"):
            async with token_accounting.track_llm_usage(
                db, user, source="insight-generation"
            ):
                _call(
                    input_tokens=500,
                    output_tokens=50,
                    cached_tokens=0,
                    total_tokens=550,
                )
            _call(input_tokens=100, output_tokens=10, cached_tokens=0, total_tokens=110)
        await db.commit()

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
    assert user.consumed_tokens == 660  # 550 inner + 110 outer, not 1210


@pytest.mark.asyncio
async def test_usage_is_persisted_even_when_the_block_raises():
    """A rate limit becoming an HTTPException must not lose what was spent (#449)."""
    user_id = await _create_user("accounting-raises@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        with pytest.raises(RuntimeError, match="boom"):
            async with token_accounting.track_llm_usage(db, user, source="api:x"):
                _call(
                    input_tokens=90, output_tokens=9, cached_tokens=0, total_tokens=99
                )
                raise RuntimeError("boom")
        await db.commit()

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
    assert user.consumed_tokens == 99


@pytest.mark.asyncio
async def test_a_persistence_failure_never_masks_the_blocks_own_error(monkeypatch):
    async def explode(*args, **kwargs):
        raise RuntimeError("db is down")

    monkeypatch.setattr(crud, "increment_user_consumed_tokens", explode)
    user_id = await _create_user("accounting-persist-fails@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        with pytest.raises(ValueError, match="the real error"):
            async with token_accounting.track_llm_usage(db, user, source="api:x"):
                _call()
                raise ValueError("the real error")


@pytest.mark.asyncio
async def test_the_scope_is_restored_after_it_closes():
    """A leaked scope would bill one request's tokens to the next one."""
    user_id = await _create_user("accounting-restore@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        async with token_accounting.track_llm_usage(db, user, source="api:x"):
            assert token_accounting.current_source() == "api:x"
        assert token_accounting.current_source() == token_accounting.UNSCOPED


def test_json_repair_is_reported_against_the_call_that_caused_it(caplog):
    token = token_accounting.begin_collection("api:generate_plan")
    try:
        _call(task="plan", model="gemini-3.6-flash")
        with caplog.at_level(logging.WARNING, logger="services.token_accounting"):
            token_accounting.note_json_repair(1200, 1240)
    finally:
        token_accounting.finish_collection(token)

    line = next(
        r.getMessage() for r in caplog.records if "LLM json_repair" in r.getMessage()
    )
    assert "task=plan" in line
    assert "model=gemini-3.6-flash" in line
    assert "source=api:generate_plan" in line
