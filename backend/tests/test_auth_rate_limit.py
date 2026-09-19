"""Brute-force limits on the auth routes (#682).

These are a different guarantee from the AI limits in ``test_ai_rate_limit.py``:
those protect the owner's money, these protect the accounts. Until this change
every auth route was reachable at unlimited rate, including ``/admin/login`` —
which is publicly routed (``PathPrefix(/api)``, no proxy auth) and stands in
front of every user's email address, their token spend, and user deletion.

The suite disables these limits globally (``auth_rate_limit_off`` in
conftest), so every test here turns them back on explicitly and resets the
module-level limiters around itself.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from config import settings
from routers import dependencies as auth_limit_dep

_LOGIN = "/api/v1/auth/login"
_REGISTER = "/api/v1/auth/register"
_ADMIN_LOGIN = "/api/v1/admin/login"

_STRONG = "Str0ng-passphrase!"


@pytest.fixture(autouse=True)
def _reset_limiters():
    """The limiters are module state, so they leak between tests unless reset."""
    for limiter in (
        auth_limit_dep.login_limiter,
        auth_limit_dep.registration_limiter,
        auth_limit_dep.admin_login_limiter,
    ):
        limiter.reset()
    yield
    for limiter in (
        auth_limit_dep.login_limiter,
        auth_limit_dep.registration_limiter,
        auth_limit_dep.admin_login_limiter,
    ):
        limiter.reset()


@pytest.fixture
def auth_limits_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    # Generous global windows by default so a test that is about the per-email
    # or per-route limit is not silently measuring the global one instead.
    monkeypatch.setattr(settings, "login_rate_limit_global_attempts", 1000)
    monkeypatch.setattr(settings, "login_rate_limit_global_seconds", 60)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_password_guesses_for_one_account_are_refused(
    client: AsyncClient, auth_limits_on, monkeypatch
):
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 3)
    monkeypatch.setattr(settings, "login_rate_limit_seconds", 300)

    body = {"email": "victim@example.com", "password": "guess"}
    statuses = [(await client.post(_LOGIN, json=body)).status_code for _ in range(4)]

    assert statuses[:3] == [401, 401, 401], statuses
    assert statuses[3] == 429, statuses


@pytest.mark.asyncio
async def test_a_refused_login_carries_retry_after(
    client: AsyncClient, auth_limits_on, monkeypatch
):
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 1)

    body = {"email": "victim@example.com", "password": "guess"}
    await client.post(_LOGIN, json=body)
    limited = await client.post(_LOGIN, json=body)

    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_the_per_email_limit_does_not_lock_out_other_accounts(
    client: AsyncClient, auth_limits_on, monkeypatch
):
    """One account under attack must not take the rest of the users with it."""
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 2)

    attacked = {"email": "victim@example.com", "password": "guess"}
    for _ in range(3):
        await client.post(_LOGIN, json=attacked)

    other = await client.post(
        _LOGIN, json={"email": "someone-else@example.com", "password": "guess"}
    )
    assert other.status_code == 401


@pytest.mark.asyncio
async def test_case_variants_of_one_email_share_a_bucket(
    client: AsyncClient, auth_limits_on, monkeypatch
):
    """``EmailStr`` does not normalise case, so the limiter has to.

    Otherwise ``Victim@…``/``vIctim@…`` are free extra allowances for the same
    account — the limit would be bypassable by holding down shift.
    """
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 2)

    await client.post(_LOGIN, json={"email": "victim@example.com", "password": "a"})
    await client.post(_LOGIN, json={"email": "VICTIM@example.com", "password": "a"})
    third = await client.post(
        _LOGIN, json={"email": "Victim@Example.com", "password": "a"}
    )

    assert third.status_code == 429


@pytest.mark.asyncio
async def test_spraying_one_password_across_many_accounts_is_bounded(
    client: AsyncClient, monkeypatch
):
    """The per-email window is blind to the attack a credential dump enables.

    A fresh address every time never fills a per-email bucket, so the global
    window is the only thing that sees it.
    """
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 100)
    monkeypatch.setattr(settings, "login_rate_limit_global_attempts", 3)
    monkeypatch.setattr(settings, "login_rate_limit_global_seconds", 60)

    statuses = [
        (
            await client.post(
                _LOGIN, json={"email": f"user{i}@example.com", "password": "hunter2"}
            )
        ).status_code
        for i in range(4)
    ]

    assert statuses[3] == 429, statuses


@pytest.mark.asyncio
async def test_a_successful_login_also_spends_allowance(
    client: AsyncClient, auth_headers, auth_limits_on, monkeypatch
):
    """Counting only failures lets one known-good credential reset the window.

    ``auth_headers`` has registered ``rider@example.com`` with a known
    password, so this is the one place the suite can check a real success.
    """
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 1)

    body = {"email": "rider@example.com", "password": "Str0ng!Pass"}
    first = await client.post(_LOGIN, json=body)
    second = await client.post(_LOGIN, json=body)

    assert first.status_code == 200
    assert second.status_code == 429


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_account_creation_is_refused(
    client: AsyncClient, auth_limits_on, monkeypatch
):
    """Registration is public on this deployment, so it needs its own ceiling.

    Keyed globally on purpose: an attacker supplies a new address each time, so
    a per-email bucket would never fill.
    """
    monkeypatch.setattr(settings, "registration_rate_limit_attempts", 2)
    monkeypatch.setattr(settings, "registration_rate_limit_seconds", 3600)

    statuses = [
        (
            await client.post(
                _REGISTER,
                json={
                    "name": f"Rider {i}",
                    "email": f"new-rider-{i}@example.com",
                    "password": _STRONG,
                },
            )
        ).status_code
        for i in range(3)
    ]

    assert statuses[:2] == [200, 200], statuses
    assert statuses[2] == 429, statuses


# ---------------------------------------------------------------------------
# Admin login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_password_guessing_is_refused(
    client: AsyncClient, auth_limits_on, monkeypatch
):
    monkeypatch.setattr(settings, "admin_password", "super-secret")
    monkeypatch.setattr(settings, "admin_login_rate_limit_attempts", 3)
    monkeypatch.setattr(settings, "admin_login_rate_limit_seconds", 900)

    statuses = [
        (await client.post(_ADMIN_LOGIN, json={"password": f"guess{i}"})).status_code
        for i in range(4)
    ]

    assert statuses[:3] == [401, 401, 401], statuses
    assert statuses[3] == 429, statuses


@pytest.mark.asyncio
async def test_a_disabled_admin_panel_does_not_spend_the_allowance(
    client: AsyncClient, auth_limits_on, monkeypatch
):
    """503 comes first, so probing a disabled panel cannot exhaust the window.

    If it did, an unauthenticated stranger could keep the operator locked out
    of a panel that was never enabled — a lockout with no attack behind it.
    """
    monkeypatch.setattr(settings, "admin_password", "")
    monkeypatch.setattr(settings, "admin_login_rate_limit_attempts", 2)

    for _ in range(5):
        resp = await client.post(_ADMIN_LOGIN, json={"password": "x"})
        assert resp.status_code == 503

    monkeypatch.setattr(settings, "admin_password", "super-secret")
    allowed = await client.post(_ADMIN_LOGIN, json={"password": "super-secret"})
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_a_non_ascii_admin_password_is_compared_not_crashed(
    client: AsyncClient, auth_limits_on, monkeypatch
):
    """``compare_digest`` raises TypeError on a non-ASCII ``str``.

    An operator who picks a passphrase with an umlaut would otherwise get a 500
    on every attempt, including their own correct one.
    """
    monkeypatch.setattr(settings, "admin_password", "schlüssel-für-alles")

    wrong = await client.post(_ADMIN_LOGIN, json={"password": "falsch"})
    assert wrong.status_code == 401

    right = await client.post(_ADMIN_LOGIN, json={"password": "schlüssel-für-alles"})
    assert right.status_code == 200


# ---------------------------------------------------------------------------
# The off switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_auth_limits_can_be_turned_off(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", False)
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 1)
    monkeypatch.setattr(settings, "login_rate_limit_global_attempts", 1)

    body = {"email": "victim@example.com", "password": "guess"}
    statuses = [(await client.post(_LOGIN, json=body)).status_code for _ in range(4)]

    assert 429 not in statuses, statuses
