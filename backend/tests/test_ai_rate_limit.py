"""AI spend controls: the per-user rate limit and the token budget (#676).

Two limits that stop different things, so they are tested apart: the rate limit
stops a burst, the budget stops a slow drip that never trips the burst. Both
must refuse *before* the provider is called — a limit that fires after the money
is spent is decoration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from config import settings
from routers import dependencies as rate_limit_dep
from services.rate_limit import SlidingWindowLimiter, Window
from services.token_accounting import (
    TokenBudgetExceededError,
    enforce_token_budget,
    track_llm_usage,
)
from tests.conftest import TestSessionLocal

_READINESS = "/api/v1/ai/readiness-score"


@pytest.fixture(autouse=True)
def _reset_limiter():
    """The limiter is module state, so it leaks between tests unless reset."""
    rate_limit_dep.ai_limiter.reset()
    yield
    rate_limit_dep.ai_limiter.reset()


# ---------------------------------------------------------------------------
# The limiter itself
# ---------------------------------------------------------------------------


def test_requests_under_the_limit_are_all_allowed():
    limiter = SlidingWindowLimiter()
    windows = (Window(3, 60),)
    assert [limiter.check("u", windows, now=t) for t in (0.0, 1.0, 2.0)] == [
        None,
        None,
        None,
    ]


def test_the_request_over_the_limit_is_refused_with_a_wait():
    limiter = SlidingWindowLimiter()
    windows = (Window(3, 60),)
    for t in (0.0, 1.0, 2.0):
        limiter.check("u", windows, now=t)

    # The fourth request in the window waits for the first one to age out.
    assert limiter.check("u", windows, now=3.0) == pytest.approx(57.0)


def test_a_refused_request_is_not_recorded():
    """Hammering while limited must not push the client's own recovery out.

    If a refused attempt were appended to the window, a client retrying in a
    loop would never recover — each retry would reset the wait. That is the
    difference between a rate limit and a lockout.
    """
    limiter = SlidingWindowLimiter()
    windows = (Window(2, 60),)
    limiter.check("u", windows, now=0.0)
    limiter.check("u", windows, now=1.0)

    for t in (2.0, 3.0, 4.0, 5.0):
        assert limiter.check("u", windows, now=t) is not None

    # The first hit ages out at t=60 and the slot is free, despite four
    # refused attempts in between.
    assert limiter.check("u", windows, now=60.5) is None


def test_the_window_slides_rather_than_resetting_on_a_boundary():
    limiter = SlidingWindowLimiter()
    windows = (Window(2, 10),)
    limiter.check("u", windows, now=0.0)
    limiter.check("u", windows, now=9.0)

    # At t=10.5 the first hit has aged out but the second has not, so exactly
    # one slot is free — a fixed bucket would have freed both.
    assert limiter.check("u", windows, now=10.5) is None
    assert limiter.check("u", windows, now=10.6) is not None


def test_the_tighter_of_two_windows_wins():
    limiter = SlidingWindowLimiter()
    windows = (Window(5, 60), Window(10, 3600))
    for t in range(5):
        assert limiter.check("u", windows, now=float(t)) is None

    # Under the sustained limit (5 of 10) but at the burst limit.
    assert limiter.check("u", windows, now=5.0) is not None


def test_the_sustained_window_catches_a_drip_that_never_trips_the_burst():
    """One request every 30 s never fills a 5-per-60 s burst window."""
    limiter = SlidingWindowLimiter()
    windows = (Window(5, 60), Window(10, 3600))

    refusals = [
        limiter.check("u", windows, now=float(i * 30)) is not None for i in range(14)
    ]
    assert any(refusals), "a slow drip escaped both windows"


def test_users_are_limited_independently():
    limiter = SlidingWindowLimiter()
    windows = (Window(1, 60),)
    assert limiter.check("a", windows, now=0.0) is None
    assert limiter.check("a", windows, now=1.0) is not None
    assert limiter.check("b", windows, now=1.0) is None


def test_idle_buckets_are_pruned_so_the_store_stays_bounded():
    limiter = SlidingWindowLimiter(max_buckets=2)
    windows = (Window(5, 10),)
    limiter.check("a", windows, now=0.0)
    limiter.check("b", windows, now=0.0)
    # Far enough ahead that a and b have aged out of the longest window.
    limiter.check("c", windows, now=100.0)
    assert set(limiter._hits) == {"c"}


def test_no_windows_means_no_limit():
    """Configuring nothing must not become an accidental limit of zero."""
    limiter = SlidingWindowLimiter()
    assert limiter.check("u", (), now=0.0) is None
    assert limiter.check("u", (), now=0.0) is None


def test_resetting_one_key_leaves_the_others_alone():
    limiter = SlidingWindowLimiter()
    windows = (Window(1, 60),)
    limiter.check("a", windows, now=0.0)
    limiter.check("b", windows, now=0.0)

    limiter.reset("a")

    assert limiter.check("a", windows, now=1.0) is None
    assert limiter.check("b", windows, now=1.0) is not None


def test_the_store_is_capped_even_when_every_bucket_is_active():
    """Age eviction alone cannot bound the store if every user is live.

    The cap is what stops a busy deployment growing one deque per user without
    limit. Eviction is oldest-idle-first, so it can only ever hand allowance
    back — never take it away from someone who has not spent it.
    """
    limiter = SlidingWindowLimiter(max_buckets=2)
    windows = (Window(5, 1000),)
    for index, key in enumerate(("a", "b", "c", "d")):
        limiter.check(key, windows, now=float(index))

    assert len(limiter._hits) <= 2
    # The most recent caller survives; the oldest is the one dropped.
    assert "d" in limiter._hits
    assert "a" not in limiter._hits


def test_a_window_must_have_a_sane_limit():
    with pytest.raises(ValueError):
        Window(0, 60)
    with pytest.raises(ValueError):
        Window(5, 0)


# ---------------------------------------------------------------------------
# The limit as the API enforces it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_burst_on_an_ai_route_is_refused_with_429_and_retry_after(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The limit is checked in a router dependency, before the route body.

    So this holds whatever the endpoint itself would have answered — which is
    the point: a refused request must not reach the work it would have paid for.
    """
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 3)
    monkeypatch.setattr(settings, "ai_rate_limit_burst_seconds", 60)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    statuses = [
        (await client.get(_READINESS, headers=auth_headers)).status_code
        for _ in range(4)
    ]

    assert statuses[-1] == 429, statuses
    assert 429 not in statuses[:3], statuses

    limited = await client.get(_READINESS, headers=auth_headers)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_the_limit_can_be_turned_off(
    client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", False)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 1)

    statuses = [
        (await client.get(_READINESS, headers=auth_headers)).status_code
        for _ in range(3)
    ]
    assert 429 not in statuses


@pytest.mark.asyncio
async def test_the_limit_does_not_apply_to_non_spending_routes(
    client: AsyncClient, auth_headers, monkeypatch
):
    """A tight AI limit must not lock the athlete out of their own profile."""
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 1)

    statuses = [
        (await client.get("/api/v1/users/me", headers=auth_headers)).status_code
        for _ in range(4)
    ]
    assert 429 not in statuses


@pytest.mark.asyncio
async def test_upload_fit_is_limited_even_though_it_is_not_on_the_ai_router(
    client: AsyncClient, auth_headers, monkeypatch
):
    """``upload-fit`` runs ``api:analyse-fit-import``, so it spends real tokens.

    It lives on ``/users/me``, which is exactly why a router-wide limit is not
    enough on its own — and why the limiter is shared rather than owned by the
    AI router, so alternating between the two cannot buy a fresh allowance.
    """
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 2)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    files = {"file": ("ride.fit", b"not really a fit file", "application/octet-stream")}
    statuses = [
        (
            await client.post(
                "/api/v1/users/me/upload-fit", headers=auth_headers, files=files
            )
        ).status_code
        for _ in range(3)
    ]

    assert statuses[-1] == 429, statuses
    assert 429 not in statuses[:2], statuses


@pytest.mark.asyncio
async def test_the_two_routers_share_one_allowance(
    client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 2)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    first = await client.get(_READINESS, headers=auth_headers)
    assert first.status_code != 429

    files = {"file": ("ride.fit", b"not really a fit file", "application/octet-stream")}
    second = await client.post(
        "/api/v1/users/me/upload-fit", headers=auth_headers, files=files
    )
    assert second.status_code != 429

    # Two spent across both routers, so the third is refused wherever it lands.
    third = await client.get(_READINESS, headers=auth_headers)
    assert third.status_code == 429


# ---------------------------------------------------------------------------
# The token budget
# ---------------------------------------------------------------------------


async def _record_spend(db, user_id: str, *, tokens: int, age_days: float = 0.0):
    db.add(
        models.LlmCall(
            user_id=user_id,
            created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
            task="coach",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            source="api:ask-trainer",
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


@pytest_asyncio.fixture
async def budget_user(db: AsyncSession) -> models.User:
    user = await crud.create_user(
        db,
        email="budget@example.com",
        name="Budget Rider",
        hashed_password="x",
    )
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_a_user_over_budget_is_refused(db, budget_user, monkeypatch):
    monkeypatch.setattr(settings, "ai_token_budget", 1000)
    monkeypatch.setattr(settings, "ai_token_budget_window_days", 30)
    await _record_spend(db, budget_user.id, tokens=1200)

    with pytest.raises(TokenBudgetExceededError) as exc:
        await enforce_token_budget(db, budget_user, source="api:ask-trainer")

    assert exc.value.spent == 1200
    assert exc.value.budget == 1000


@pytest.mark.asyncio
async def test_a_user_under_budget_passes(db, budget_user, monkeypatch):
    monkeypatch.setattr(settings, "ai_token_budget", 1000)
    await _record_spend(db, budget_user.id, tokens=999)

    await enforce_token_budget(db, budget_user, source="api:ask-trainer")


@pytest.mark.asyncio
async def test_spend_outside_the_window_does_not_count(
    db, budget_user, monkeypatch
):
    """The budget is a rolling window, so it recovers without a reset job."""
    monkeypatch.setattr(settings, "ai_token_budget", 1000)
    monkeypatch.setattr(settings, "ai_token_budget_window_days", 30)
    await _record_spend(db, budget_user.id, tokens=5000, age_days=31)

    await enforce_token_budget(db, budget_user, source="api:ask-trainer")


@pytest.mark.asyncio
async def test_a_zero_budget_disables_the_check(db, budget_user, monkeypatch):
    monkeypatch.setattr(settings, "ai_token_budget", 0)
    await _record_spend(db, budget_user.id, tokens=10_000_000)

    await enforce_token_budget(db, budget_user, source="api:ask-trainer")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    ["job:plan-maintenance", "step:coach-narration", "bg:update-coach-memory"],
)
async def test_only_api_sources_are_gated(
    db, budget_user, monkeypatch, source
):
    """Scheduled and background work must not be killed by the budget.

    A nightly plan-maintenance run silently skipped because the athlete chatted
    a lot is the #472 failure mode — work the athlete never asked to lose,
    disappearing without a message. Requests are what a user can spam, and
    background work is bounded by the request that spawned it, so gating the
    request gates the chain.
    """
    monkeypatch.setattr(settings, "ai_token_budget", 1000)
    await _record_spend(db, budget_user.id, tokens=50_000)

    await enforce_token_budget(db, budget_user, source=source)


@pytest.mark.asyncio
async def test_an_over_budget_request_answers_402_not_429(
    client: AsyncClient, auth_headers, mock_ai_service, db: AsyncSession, monkeypatch
):
    """The refusal the athlete actually receives, over HTTP.

    The unit tests above prove the gate raises; they say nothing about what the
    client is told, which is the half that matters to the UI. 402 rather than
    429 is the point of the exception handler: a budget frees up as old usage
    ages out of a rolling window — hours to days — so a client that treats it
    as "retry shortly" would hammer a wall.
    """
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = me.json()["id"]

    monkeypatch.setattr(settings, "ai_token_budget", 1000)
    monkeypatch.setattr(settings, "ai_token_budget_window_days", 30)
    await _record_spend(db, user_id, tokens=5000)
    await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "What should I ride today?"},
    )

    assert response.status_code == 402, response.text
    assert "budget" in response.json()["detail"].lower()
    # Not the rate limit: no Retry-After, because there is nothing to retry into.
    assert "Retry-After" not in response.headers


@pytest.mark.asyncio
async def test_a_request_within_budget_is_not_refused(
    client: AsyncClient, auth_headers, mock_ai_service, db: AsyncSession, monkeypatch
):
    """The other half: the gate must not refuse someone who is under budget.

    Without this, the test above would pass just as well if every request 402'd.
    """
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = me.json()["id"]

    monkeypatch.setattr(settings, "ai_token_budget", 1_000_000)
    await _record_spend(db, user_id, tokens=5000)
    await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "What should I ride today?"},
    )

    assert response.status_code != 402


@pytest.mark.asyncio
async def test_the_budget_is_enforced_by_the_usage_scope_itself(
    db, budget_user, monkeypatch
):
    """The gate is inside ``track_llm_usage``, not at each of its call sites.

    Fifteen call sites cannot each be relied on to remember a check, which is
    the same argument that put attribution in this context manager.
    """
    monkeypatch.setattr(settings, "ai_token_budget", 1000)
    await _record_spend(db, budget_user.id, tokens=2000)

    entered = False
    with pytest.raises(TokenBudgetExceededError):
        async with track_llm_usage(
            db, budget_user, source="api:ask-trainer"
        ):
            entered = True

    assert not entered, "the budget was checked after the block had already run"
