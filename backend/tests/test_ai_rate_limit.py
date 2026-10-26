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
    GlobalTokenBudgetExceededError,
    TokenBudgetExceededError,
    enforce_token_budget,
    track_llm_usage,
)

_READINESS = "/api/v1/ai/readiness-score"
# A token-spending POST on the AI router: the limiter counts it, unlike the
# cheap GETs, which read already-derived data and are exempt (ops#132).
_ASK = "/api/v1/ai/ask-trainer"
_ASK_BODY = {"question": "What should I ride today?"}


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
    client: AsyncClient, auth_headers, mock_ai_service, monkeypatch
):
    """The limit is checked in a router dependency, before the route body.

    So this holds whatever the endpoint itself would have answered — which is
    the point: a refused request must not reach the work it would have paid for.
    Driven through a POST, since the token-spending verbs are what the limiter
    now counts (ops#132).
    """
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 3)
    monkeypatch.setattr(settings, "ai_rate_limit_burst_seconds", 60)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    statuses = [
        (await client.post(_ASK, headers=auth_headers, json=_ASK_BODY)).status_code
        for _ in range(4)
    ]

    assert statuses[-1] == 429, statuses
    assert 429 not in statuses[:3], statuses

    limited = await client.post(_ASK, headers=auth_headers, json=_ASK_BODY)
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
async def test_the_limit_does_not_apply_to_ai_get_routes(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The AI GETs read already-derived data and spend nothing, so a tight
    limit must not 429 them and lock the dashboard that fans out to several of
    them on every load (ops#132)."""
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 1)

    statuses = [
        (await client.get(_READINESS, headers=auth_headers)).status_code
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
async def test_bulk_upload_fit_is_limited_too(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The bulk route was the one #676 missed (#682).

    It spends through the same ``_store_fit_import`` as its single-file twin,
    so an unlimited bulk route made the limit on the single one decorative —
    the same work was reachable one URL over.
    """
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 2)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    files = [
        ("files", ("ride.fit", b"not really a fit file", "application/octet-stream"))
    ]
    statuses = [
        (
            await client.post(
                "/api/v1/users/me/upload-fit/bulk", headers=auth_headers, files=files
            )
        ).status_code
        for _ in range(3)
    ]

    assert statuses[-1] == 429, statuses
    assert 429 not in statuses[:2], statuses


def _parse_as_todays_rides(monkeypatch) -> None:
    """Every file parses as a distinct ride of today's.

    Only a parsed, recent ride is analysed, so only it pays (ai-trainer-ops#157).
    A bogus payload fails to parse first and would make a charge test pass or
    fail for the wrong reason.
    """
    import dataclasses
    import itertools

    from services import fit_import
    from tests.test_byok import _fake_parsed_fit

    ids = itertools.count(1)
    monkeypatch.setattr(
        fit_import,
        "parse_fit_activity",
        lambda raw, name, FitFile: dataclasses.replace(
            _fake_parsed_fit(), source_id=next(ids)
        ),
    )


@pytest.mark.asyncio
async def test_a_bulk_batch_pays_per_file_not_per_request(
    client: AsyncClient, auth_headers, monkeypatch
):
    """Otherwise batch size is a way to buy AI calls at a flat rate of one.

    Each file in the batch is its own ``_analyse_fit_import`` call, so each
    file after the first spends from the same allowance. Hitting the limit
    stops the batch rather than failing it: what was already imported stays
    imported, and the response names the files that were not attempted.
    """
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 2)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)
    _parse_as_todays_rides(monkeypatch)

    files = [
        (
            "files",
            (f"ride{i}.fit", b"not really a fit file", "application/octet-stream"),
        )
        for i in range(3)
    ]
    resp = await client.post(
        "/api/v1/users/me/upload-fit/bulk", headers=auth_headers, files=files
    )

    assert resp.status_code == 200
    results = resp.json()["files"]
    assert len(results) == 3, results
    # The request itself paid for file 1; file 2 paid its own way; file 3 found
    # the allowance spent.
    assert "rate limit" in results[-1]["message"].lower(), results


@pytest.mark.asyncio
async def test_the_two_routers_share_one_allowance(
    client: AsyncClient, auth_headers, mock_ai_service, monkeypatch
):
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 2)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    # A token-spending POST on the AI router, not a GET (ops#132).
    first = await client.post(_ASK, headers=auth_headers, json=_ASK_BODY)
    assert first.status_code != 429

    files = {"file": ("ride.fit", b"not really a fit file", "application/octet-stream")}
    second = await client.post(
        "/api/v1/users/me/upload-fit", headers=auth_headers, files=files
    )
    assert second.status_code != 429

    # Two spent across both routers, so the third is refused wherever it lands.
    third = await client.post(_ASK, headers=auth_headers, json=_ASK_BODY)
    assert third.status_code == 429


# ---------------------------------------------------------------------------
# The token budget
# ---------------------------------------------------------------------------


async def _record_spend(db, user_id: str | None, *, tokens: int, age_days: float = 0.0):
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
async def test_spend_outside_the_window_does_not_count(db, budget_user, monkeypatch):
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
async def test_only_api_sources_are_gated(db, budget_user, monkeypatch, source):
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
    detail = response.json()["detail"]
    # What to do about it, in words the athlete can act on (ai-trainer-ops#79).
    assert "free coaching allowance" in detail
    assert "own AI key" in detail
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
        async with track_llm_usage(db, budget_user, source="api:ask-trainer"):
            entered = True

    assert not entered, "the budget was checked after the block had already run"


# ---------------------------------------------------------------------------
# The free tier rations the owner's key only (ai-trainer-ops#79)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_athlete_with_their_own_key_is_never_limited(
    db, budget_user, monkeypatch
):
    monkeypatch.setattr(settings, "ai_token_budget", 1000)
    budget_user.ai_provider = "gemini"
    budget_user.user_gemini_api_key = "own-key"
    await _record_spend(db, budget_user.id, tokens=50_000)

    await enforce_token_budget(db, budget_user, source="api:ask-trainer")


@pytest.mark.asyncio
async def test_settings_show_the_allowance_while_the_owner_pays(
    client: AsyncClient, auth_headers, db: AsyncSession, monkeypatch
):
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    monkeypatch.setattr(settings, "ai_token_budget", 5_000_000)
    await _record_spend(db, me.json()["id"], tokens=1_234)
    await db.commit()

    status = (
        await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)
    ).json()

    assert status["allowanceTokens"] == 5_000_000
    assert status["allowanceUsed"] == 1_234
    assert status["allowanceWindowDays"] == 30


@pytest.mark.asyncio
async def test_settings_show_no_allowance_with_an_own_key(
    client: AsyncClient, auth_headers, db: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "ai_token_budget", 5_000_000)
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user = await db.get(models.User, me.json()["id"])
    user.ai_provider = "gemini"
    user.user_gemini_api_key = "own-key"
    await db.commit()

    status = (
        await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)
    ).json()

    assert status["allowanceTokens"] is None


# ---------------------------------------------------------------------------
# The global daily cap on the owner's key (ai-trainer-ops#39)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_daily_cap_counts_every_free_account(db, budget_user, monkeypatch):
    """Many accounts, each under its own budget, still stop at the daily cap."""
    monkeypatch.setattr(settings, "ai_token_budget", 1_000_000)
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 1000)
    other = await crud.create_user(
        db, email="other@example.com", name="Other Rider", hashed_password="x"
    )
    await db.flush()
    await _record_spend(db, other.id, tokens=600)
    await _record_spend(db, budget_user.id, tokens=300)

    await enforce_token_budget(db, budget_user, source="api:ask-trainer")

    await _record_spend(db, other.id, tokens=200)
    with pytest.raises(GlobalTokenBudgetExceededError):
        await enforce_token_budget(db, budget_user, source="api:ask-trainer")


@pytest.mark.asyncio
async def test_the_owner_hears_when_the_daily_cap_is_reached(
    db, budget_user, monkeypatch
):
    """ai-trainer-ops#124: every tester without an own key now gets no coach
    replies, and a background job stopping at the cap tells the owner too."""
    sent: list[tuple[str, str]] = []

    async def notify_once(key: str, title: str, message: str) -> bool:
        sent.append((key, message))
        return True

    monkeypatch.setattr("services.owner_alerts.notify_once", notify_once)
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 1000)
    await _record_spend(db, budget_user.id, tokens=900)

    await enforce_token_budget(db, budget_user, source="api:ask-trainer")
    assert sent == []

    await _record_spend(db, budget_user.id, tokens=200)
    with pytest.raises(GlobalTokenBudgetExceededError):
        await enforce_token_budget(db, budget_user, source="nightly-plan-maintenance")
    assert [key for key, _ in sent] == ["ai-global-daily-budget"]
    assert "1,100 of 1,000 tokens" in sent[0][1]


@pytest.mark.asyncio
async def test_the_daily_cap_ignores_own_keys_and_yesterday(
    db, budget_user, monkeypatch
):
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 1000)
    byok = await crud.create_user(
        db, email="byok@example.com", name="Own Key", hashed_password="x"
    )
    byok.user_gemini_api_key = "own-key"
    await db.flush()
    await _record_spend(db, byok.id, tokens=50_000)
    await _record_spend(db, budget_user.id, tokens=50_000, age_days=1.1)

    await enforce_token_budget(db, budget_user, source="api:ask-trainer")

    await _record_spend(db, budget_user.id, tokens=5000)
    with pytest.raises(GlobalTokenBudgetExceededError):
        await enforce_token_budget(db, budget_user, source="api:ask-trainer")
    # An athlete with their own key is not stopped by the owner's cap.
    byok.ai_provider = "gemini"
    await enforce_token_budget(db, byok, source="api:ask-trainer")


@pytest.mark.asyncio
async def test_the_daily_cap_counts_unattributed_calls_and_other_provider_keys(
    db, budget_user, monkeypatch
):
    """Only the per-user budget is off: the daily cap still applies alone.

    A key for the other provider does not pay for a Gemini call, and a call
    billed to nobody was paid by the owner.
    """
    monkeypatch.setattr(settings, "ai_token_budget", 0)
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 1000)
    openai_only = await crud.create_user(
        db, email="openai@example.com", name="OpenAI Key", hashed_password="x"
    )
    openai_only.user_openai_api_key = "own-key"
    await db.flush()
    await _record_spend(db, openai_only.id, tokens=600)  # a Gemini call
    await enforce_token_budget(db, budget_user, source="api:ask-trainer")

    await _record_spend(db, None, tokens=400)
    with pytest.raises(GlobalTokenBudgetExceededError):
        await enforce_token_budget(db, budget_user, source="api:ask-trainer")


# ---------------------------------------------------------------------------
# What the app asks for by itself has its own allowance (ai-trainer-ops#183)
# ---------------------------------------------------------------------------

_APP_CALLS = (
    "/api/v1/ai/plan-translations",
    "/api/v1/ai/refresh-login-summary",
    "/api/v1/ai/season-plan/goals",
)


def _tight_limit(monkeypatch, burst: int = 3) -> None:
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", burst)
    monkeypatch.setattr(settings, "ai_rate_limit_burst_seconds", 60)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)


@pytest.mark.asyncio
async def test_translations_summaries_and_goals_do_not_spend_the_chat_allowance(
    client: AsyncClient, auth_headers, mock_ai_service, monkeypatch
):
    """Lena's first minute (persona run, 2026-10-21): the German interface's own
    calls left the chat two requests, and the third message was refused."""
    _tight_limit(monkeypatch)
    for path in _APP_CALLS:
        # A 404 for a new account's summary spends nothing of the chat either.
        assert (
            await client.post(path, headers=auth_headers, json={})
        ).status_code != 429

    statuses = [
        (await client.post(_ASK, headers=auth_headers, json=_ASK_BODY)).status_code
        for _ in range(3)
    ]

    assert 429 not in statuses, statuses


@pytest.mark.asyncio
async def test_the_apps_own_calls_are_still_limited(
    client: AsyncClient, auth_headers, mock_ai_service, monkeypatch
):
    _tight_limit(monkeypatch, burst=2)
    path = "/api/v1/ai/plan-translations"
    statuses = [
        (await client.post(path, headers=auth_headers, json={})).status_code
        for _ in range(3)
    ]

    assert statuses[-1] == 429 and 429 not in statuses[:2], statuses
    # And the chat still has its whole allowance.
    assert (
        await client.post(_ASK, headers=auth_headers, json=_ASK_BODY)
    ).status_code != 429
