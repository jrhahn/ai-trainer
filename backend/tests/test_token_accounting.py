"""Tests for per-call LLM cost accounting (#516).

The point of the feature is that a log line can answer "which feature is
costing me money" and that no provider call is billed to nobody, so that is
what these assert: the shape of the line, the input/output/cached split
reaching the user's counters, and the paths that previously escaped a scope.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select

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


def test_a_call_outside_any_scope_is_a_warning_not_a_detail(caplog):
    """#537 hid for a month inside a routine INFO line; it now warns."""
    with caplog.at_level(logging.WARNING, logger="services.token_accounting"):
        _call()
    warning = next(
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "billed to nobody" in r.getMessage()
    )
    assert "task=coach" in warning
    assert "prompt_sha=" in warning


@pytest.mark.asyncio
async def test_a_scoped_call_does_not_warn(caplog):
    """The warning is only useful if a correct call is silent."""
    user_id = await _create_user("accounting-no-warning@example.com")
    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        with caplog.at_level(logging.WARNING, logger="services.token_accounting"):
            async with token_accounting.track_llm_usage(db, user, source="api:x"):
                _call()
    assert not any("billed to nobody" in r.getMessage() for r in caplog.records)


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
        async with token_accounting.track_llm_usage(db, user, source="api:ask-trainer"):
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
                db, user, source="step:insight-generation"
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
async def test_usage_from_a_raising_block_survives_the_rollback():
    """A rate limit becoming an HTTPException must not lose what was spent.

    The spend cannot be written where it was collected: that transaction is
    about to be rolled back and would take the rows with it, which is why three
    failed coach requests billed ~52k tokens and left no row at all (#560). It
    is deferred onto the session and written once the transaction is resolved —
    here, after the rollback that stands in for the failed request.
    """
    user_id = await _create_user("accounting-raises@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        with pytest.raises(RuntimeError, match="boom"):
            async with token_accounting.track_llm_usage(db, user, source="api:x"):
                _call(
                    input_tokens=90, output_tokens=9, cached_tokens=0, total_tokens=99
                )
                raise RuntimeError("boom")
        # What the failed request's own teardown does.
        await db.rollback()
        await token_accounting.flush_deferred_usage(db)

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        calls = (
            await db.execute(
                select(models.LlmCall).where(models.LlmCall.user_id == user_id)
            )
        ).scalars().all()

    assert user.consumed_tokens == 99
    assert [call.source for call in calls] == ["api:x"]


@pytest.mark.asyncio
async def test_a_failed_request_still_records_what_it_spent(
    client, auth_headers, mock_ai_service
):
    """The whole point of #560, end to end rather than at ``_persist_usage``.

    Three coach failures logged their spend and wrote nothing: the endpoint
    raised, the request transaction rolled back, and it took the ``llm_calls``
    rows with it. A model that has started rejecting every request is exactly
    what a cost table has to be able to show, and it was the one case invisible
    in it.
    """
    from auth import decode_token

    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    async def spend_then_fail(*args, **kwargs):
        # The provider answered — and billed — before the failure.
        _call(task="coach", input_tokens=17_243, output_tokens=188, total_tokens=17_431)
        raise RuntimeError("model is rejecting everything")

    mock_ai_service["ask_trainer"].side_effect = spend_then_fail

    # The test transport re-raises rather than rendering the 500 the athlete
    # sees; either way the request ends in a rollback, which is what matters.
    with pytest.raises(RuntimeError, match="rejecting everything"):
        await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "Can I ride today?"},
        )

    async with TestSessionLocal() as db:
        calls = (
            await db.execute(
                select(models.LlmCall).where(models.LlmCall.user_id == user_id)
            )
        ).scalars().all()
        user = await db.get(models.User, user_id)

    assert [call.source for call in calls] == ["api:ask-trainer"]
    assert calls[0].input_tokens == 17_243
    assert user.consumed_tokens == 17_431
    assert user.consumed_input_tokens == 17_243


@pytest.mark.asyncio
async def test_a_scope_that_succeeds_defers_nothing():
    """The normal path still writes inline, inside the caller's transaction."""
    user_id = await _create_user("accounting-no-defer@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        async with token_accounting.track_llm_usage(db, user, source="api:x"):
            _call(input_tokens=10, output_tokens=1, cached_tokens=0, total_tokens=11)
        assert token_accounting._DEFERRED_USAGE_KEY not in db.info
        await db.commit()

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
    assert user.consumed_tokens == 11


@pytest.mark.asyncio
async def test_flushing_twice_bills_once():
    """The flush is what a ``finally`` calls, so it has to be safe to call."""
    user_id = await _create_user("accounting-flush-twice@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        with pytest.raises(RuntimeError):
            async with token_accounting.track_llm_usage(db, user, source="api:x"):
                _call(input_tokens=90, output_tokens=9, cached_tokens=0, total_tokens=99)
                raise RuntimeError("boom")
        await db.rollback()
        await token_accounting.flush_deferred_usage(db)
        await token_accounting.flush_deferred_usage(db)

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
    assert user.consumed_tokens == 99


@pytest.mark.asyncio
async def test_a_failing_flush_never_masks_the_error_that_caused_it(monkeypatch):
    """The flush runs in a ``finally`` while an exception is in flight."""
    user_id = await _create_user("accounting-flush-fails@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        with pytest.raises(RuntimeError, match="boom"):
            async with token_accounting.track_llm_usage(db, user, source="api:x"):
                _call(input_tokens=90, output_tokens=9, cached_tokens=0, total_tokens=99)
                raise RuntimeError("boom")
        await db.rollback()

        async def explode(*args, **kwargs):
            raise RuntimeError("db is down")

        monkeypatch.setattr(crud, "record_llm_calls", explode)
        # Swallowed, not raised: the caller is already handling a real failure.
        await token_accounting.flush_deferred_usage(db)


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


# ---------------------------------------------------------------------------
# Work that outlives its session (#537)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detached_usage_is_billed_without_a_caller_session():
    user_id = await _create_user("accounting-detached@example.com")

    async with token_accounting.track_llm_usage_detached(
        TestSessionLocal, user_id, source="bg:update-coach-memory"
    ):
        _call(input_tokens=2088, output_tokens=881, cached_tokens=0, total_tokens=2969)

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
    assert user.consumed_tokens == 2969
    assert user.consumed_input_tokens == 2088
    assert user.consumed_output_tokens == 881


@pytest.mark.asyncio
async def test_detached_usage_holds_no_session_during_the_call():
    """The whole point of the detached variant (#346/#522).

    A session opened around a provider call would hold a transaction for the
    seconds it takes to generate, which is exactly what the memory update goes
    out of its way to avoid.
    """
    opened: list[str] = []

    def tracking_session_maker():
        opened.append("open")
        return TestSessionLocal()

    user_id = await _create_user("accounting-detached-late@example.com")
    async with token_accounting.track_llm_usage_detached(
        tracking_session_maker, user_id, source="bg:x"
    ):
        assert opened == []  # nothing open while the provider is working
        _call()
    assert opened == ["open"]


@pytest.mark.asyncio
async def test_detached_usage_with_nothing_spent_opens_no_session():
    """A no-op background task must not pay for a connection."""
    opened: list[str] = []

    def tracking_session_maker():
        opened.append("open")
        return TestSessionLocal()

    async with token_accounting.track_llm_usage_detached(
        tracking_session_maker, "irrelevant", source="bg:x"
    ):
        pass
    assert opened == []


@pytest.mark.asyncio
async def test_detached_usage_survives_a_deleted_user(caplog):
    """Billing a user who left must be reported, not raised at the task."""
    with caplog.at_level(logging.WARNING, logger="services.token_accounting"):
        async with token_accounting.track_llm_usage_detached(
            TestSessionLocal, "no-such-user", source="bg:x"
        ):
            _call()
    assert any("user no-such-user is gone" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_detached_usage_is_persisted_even_when_the_block_raises():
    user_id = await _create_user("accounting-detached-raises@example.com")

    with pytest.raises(RuntimeError, match="boom"):
        async with token_accounting.track_llm_usage_detached(
            TestSessionLocal, user_id, source="bg:x"
        ):
            _call(input_tokens=90, output_tokens=9, cached_tokens=0, total_tokens=99)
            raise RuntimeError("boom")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
    assert user.consumed_tokens == 99


@pytest.mark.asyncio
async def test_detached_scope_is_restored_after_it_closes():
    user_id = await _create_user("accounting-detached-restore@example.com")
    async with token_accounting.track_llm_usage_detached(
        TestSessionLocal, user_id, source="bg:x"
    ):
        assert token_accounting.current_source() == "bg:x"
    assert token_accounting.current_source() == token_accounting.UNSCOPED


# ---------------------------------------------------------------------------
# Records that outlive the container (#549)
# ---------------------------------------------------------------------------


async def _stored_calls() -> list[models.LlmCall]:
    async with TestSessionLocal() as db:
        rows = await db.execute(
            select(models.LlmCall).order_by(models.LlmCall.created_at)
        )
        return list(rows.scalars())


@pytest.mark.asyncio
async def test_a_call_is_stored_with_everything_needed_to_price_it():
    user_id = await _create_user("calls-stored@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        async with token_accounting.track_llm_usage(db, user, source="api:ask-trainer"):
            _call()
        await db.commit()

    stored = await _stored_calls()
    assert len(stored) == 1
    row = stored[0]
    assert row.user_id == user_id
    assert (row.task, row.provider, row.model) == (
        "coach",
        "gemini",
        "gemini-3.5-flash-lite",
    )
    assert row.source == "api:ask-trainer"
    assert (row.input_tokens, row.output_tokens, row.cached_tokens) == (
        12_000,
        480,
        9_000,
    )
    assert row.total_tokens == 12_480
    assert row.latency_ms == 1234
    assert row.ok is True
    assert row.prompt_sha == token_accounting.prompt_fingerprint(
        "You are a cycling coach."
    )


@pytest.mark.asyncio
async def test_every_call_of_a_scope_is_stored_under_that_scopes_source():
    user_id = await _create_user("calls-many@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        async with token_accounting.track_llm_usage(db, user, source="job:sync"):
            _call(task="classify")
            _call(task="plan")
        await db.commit()

    stored = await _stored_calls()
    assert [row.task for row in stored] == ["classify", "plan"]
    assert {row.source for row in stored} == {"job:sync"}


@pytest.mark.asyncio
async def test_a_failed_call_is_stored_even_though_it_spent_nothing():
    """A model rejecting every request is what a cost table has to show (#401)."""
    user_id = await _create_user("calls-failed@example.com")

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        async with token_accounting.track_llm_usage(db, user, source="api:x"):
            _call(
                ok=False,
                error="AIRateLimitError",
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                total_tokens=0,
            )
        await db.commit()

    stored = await _stored_calls()
    assert len(stored) == 1
    assert stored[0].ok is False
    assert stored[0].error == "AIRateLimitError"
    assert stored[0].total_tokens == 0


@pytest.mark.asyncio
async def test_detached_work_stores_its_calls_too():
    user_id = await _create_user("calls-detached@example.com")

    async with token_accounting.track_llm_usage_detached(
        TestSessionLocal, user_id, source="bg:update-coach-memory"
    ):
        _call(task="classify")

    stored = await _stored_calls()
    assert len(stored) == 1
    assert stored[0].source == "bg:update-coach-memory"
    assert stored[0].user_id == user_id


@pytest.mark.asyncio
async def test_an_unscoped_call_is_warned_about_but_not_stored(caplog):
    """Known and deliberate: there is no session and no user to store it under.

    The WARNING from #537 is the signal; a call reaching this point is a bug in
    the caller, not something to file quietly under nobody.
    """
    with caplog.at_level(logging.WARNING, logger="services.token_accounting"):
        _call()
    assert any("billed to nobody" in r.getMessage() for r in caplog.records)
    assert await _stored_calls() == []


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
