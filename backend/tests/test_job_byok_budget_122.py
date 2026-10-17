"""Background jobs honour BYOK and both budgets (ops#122).

The leak: scheduler jobs set no BYOK context, so llm.get_provider fell through
to the owner's global key for every athlete — ignoring allow_admin_ai_key_fallback
— and token_accounting.enforce_token_budget returned early for anything that was
not an ``api:`` source, so job spend never counted against the global daily cap.

The fix lives at the one choke point every background spend flows through,
``track_llm_usage``: it now puts the athlete's own keys in scope for a non-``api:``
source, and ``enforce_token_budget`` counts owner-key job/bg spend against the
global cap (while the per-user rolling budget stays ``api:``-only, #472).
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import services.llm as llm
from config import settings
from services.llm import AIKeyNotConfiguredError, get_provider
from services.token_accounting import (
    GlobalTokenBudgetExceededError,
    TokenBudgetExceededError,
    enforce_token_budget,
    flushing_session,
    record_call,
    track_llm_usage,
)
from tests.conftest import TestSessionLocal


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Yield a fresh session that is rolled back after each test."""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.fixture(autouse=True)
def _stub_off(monkeypatch):
    """These assert real provider routing, so the canned stub must be inactive."""
    monkeypatch.setattr(llm, "stub_is_active", lambda: False)


async def _make_user(db, email, *, gemini_key=None):
    user = await crud.create_user(db, email=email, name="Rider", hashed_password="x")
    if gemini_key is not None:
        user.user_gemini_api_key = gemini_key
    await db.flush()
    return user


async def _record_owner_key_spend(db, user_id, *, tokens):
    """An llm_calls row the owner's key paid for (the user holds no own key)."""
    db.add(
        models.LlmCall(
            user_id=user_id,
            created_at=datetime.now(timezone.utc),
            task="coach",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            source="job:strava-sync",
            input_tokens=tokens,
            output_tokens=0,
            cached_tokens=0,
            total_tokens=tokens,
            latency_ms=1,
            json_mode=False,
            ok=True,
            prompt_sha="deadbeef",
        )
    )
    await db.flush()


async def test_job_scope_routes_to_the_athletes_own_key(db, monkeypatch):
    """Criterion 1: a job for an own-key athlete uses that athlete's key."""
    monkeypatch.setattr(settings, "ai_token_budget", 0)
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 0)
    monkeypatch.setattr(settings, "gemini_api_key", "owner-global-key")
    user = await _make_user(db, "ownkey@example.com", gemini_key="athlete-key")

    async with track_llm_usage(db, user, source="job:strava-sync"):
        provider = get_provider("gemini")
        assert provider._api_key == "athlete-key"


async def test_job_with_fallback_off_and_no_key_spends_nothing(db, monkeypatch):
    """Criterion 2: fallback off + no own key → get_provider refuses, no spend."""
    monkeypatch.setattr(settings, "ai_token_budget", 0)
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 0)
    monkeypatch.setattr(settings, "allow_admin_ai_key_fallback", False)
    monkeypatch.setattr(settings, "gemini_api_key", "owner-global-key")
    user = await _make_user(db, "nokey@example.com")

    with pytest.raises(AIKeyNotConfiguredError):
        async with track_llm_usage(db, user, source="job:strava-sync"):
            get_provider("gemini")


async def test_job_spend_counts_toward_the_global_cap(db, monkeypatch):
    """Criterion 3: a job source is gated by the global daily cap."""
    monkeypatch.setattr(settings, "ai_token_budget", 0)
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 1)
    monkeypatch.setattr(settings, "allow_admin_ai_key_fallback", True)
    user = await _make_user(db, "global@example.com")  # no own key → owner pays
    await _record_owner_key_spend(db, user.id, tokens=5)

    with pytest.raises(GlobalTokenBudgetExceededError):
        await enforce_token_budget(db, user, source="job:strava-sync")


async def test_flushing_session_persists_spend_from_a_step_that_raised(monkeypatch):
    """A step that raises after a paid call still records to llm_calls (ops#122).

    track_llm_usage defers the usage (the rows would otherwise go into a
    transaction about to roll back); flushing_session rolls back the failed
    transaction and flushes, so the spend is not lost — which is what the
    nightly and weekly runners now rely on.
    """
    monkeypatch.setattr(settings, "ai_token_budget", 0)
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 0)
    async with TestSessionLocal() as setup:
        user = await crud.create_user(
            setup, email="flush@example.com", name="Rider", hashed_password="x"
        )
        await setup.commit()
        user_id = user.id

    with pytest.raises(RuntimeError):
        async with flushing_session(TestSessionLocal) as db:
            user = await crud.get_user_by_id(db, user_id)
            async with track_llm_usage(db, user, source="job:strava-sync"):
                record_call(
                    task="coach",
                    provider="gemini",
                    model="gemini-3.5-flash-lite",
                    system_prompt="s",
                    json_mode=False,
                    latency_ms=1,
                    input_tokens=100,
                    total_tokens=100,
                )
                raise RuntimeError("boom after a paid call")

    async with TestSessionLocal() as check:
        rows = (
            await check.execute(
                models.LlmCall.__table__.select().where(
                    models.LlmCall.user_id == user_id
                )
            )
        ).all()
    assert len(rows) == 1, rows


async def test_per_user_rolling_budget_still_only_applies_to_api(db, monkeypatch):
    """#472 preserved: a chatty athlete's rolling budget must not kill a job."""
    monkeypatch.setattr(settings, "ai_token_budget", 1)
    monkeypatch.setattr(settings, "ai_token_budget_window_days", 30)
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 0)
    monkeypatch.setattr(settings, "allow_admin_ai_key_fallback", True)
    user = await _make_user(db, "rolling@example.com")  # no own key → owner pays
    await _record_owner_key_spend(db, user.id, tokens=5000)

    # The job is not gated by the per-user rolling budget …
    await enforce_token_budget(db, user, source="job:strava-sync")
    # … but an api: request from the same athlete is.
    with pytest.raises(TokenBudgetExceededError):
        await enforce_token_budget(db, user, source="api:ask-trainer")
