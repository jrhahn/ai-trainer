"""Taking back an issued JWT (#704).

The property under test is narrow and worth stating plainly: a token stops
working the moment the account it names has its sessions revoked, and keeps
working until then. Everything below is one of the ways that can go wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select

import auth
import models
from config import settings
from tests.conftest import TestSessionLocal

PASSWORD = "Str0ng!Pass"
"""The password ``conftest.auth_headers`` registers with."""

# A live, cheap, authenticated endpoint. Any would do; this one touches no
# other subsystem, so a failure here is about the token and nothing else.
PROBE = "/api/v1/auth/totp/status"


async def _register(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"name": "Rider", "email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _revoke(client: AsyncClient, token: str, password: str = PASSWORD):
    return await client.post(
        "/api/v1/auth/sessions/revoke",
        json={"password": password},
        headers=_headers(token),
    )


async def _generation(user_id: str) -> int:
    async with TestSessionLocal() as session:
        return await session.scalar(
            select(models.User.token_generation).where(models.User.id == user_id)
        )


# ---------------------------------------------------------------------------
# The athlete's own "sign out everywhere"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_token_works_until_something_revokes_it(client, auth_headers):
    """The baseline. Without this, every assertion below could pass vacuously."""
    response = await client.get(PROBE, headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_revoking_sessions_kills_the_token_that_asked(client, auth_headers):
    token = auth_headers["Authorization"].split(" ", 1)[1]

    response = await _revoke(client, token)
    assert response.status_code == 200
    assert response.json()["tokenGeneration"] == 1

    # Including this one: the athlete asked for every session, and their own
    # browser is not an exception. Anything less would leave the one session an
    # attacker is most likely to be sitting in.
    after = await client.get(PROBE, headers=auth_headers)
    assert after.status_code == 401
    assert after.json()["detail"] == auth.SESSION_REVOKED_DETAIL


@pytest.mark.asyncio
async def test_signing_in_again_after_a_revocation_works(client, auth_headers):
    token = auth_headers["Authorization"].split(" ", 1)[1]
    assert (await _revoke(client, token)).status_code == 200

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200
    fresh = login.json()["access_token"]

    # The new token carries the new generation, so the account is usable again
    # straight away. A revocation that locked the owner out permanently would
    # be a worse bug than the gap it closes.
    assert (await client.get(PROBE, headers=_headers(fresh))).status_code == 200


@pytest.mark.asyncio
async def test_a_wrong_password_revokes_nothing(client, auth_headers):
    """A failed step-up must not be a working denial-of-service.

    If the increment happened before the password check, anyone holding a
    stolen token could sign the owner out at will — turning a confidentiality
    problem into an availability one.
    """
    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = auth.decode_token(token)

    response = await _revoke(client, token, password="not-the-password")
    assert response.status_code == 401
    assert await _generation(user_id) == 0
    assert (await client.get(PROBE, headers=auth_headers)).status_code == 200


@pytest.mark.asyncio
async def test_revoking_one_account_leaves_another_alone(client, auth_headers):
    mine = auth_headers["Authorization"].split(" ", 1)[1]
    theirs = await _register(client, "other@example.com")

    assert (await _revoke(client, mine)).status_code == 200

    assert (await client.get(PROBE, headers=_headers(mine))).status_code == 401
    assert (await client.get(PROBE, headers=_headers(theirs))).status_code == 200


@pytest.mark.asyncio
async def test_a_token_from_between_two_revocations_stays_dead(client, auth_headers):
    """The counter only ever goes up, so no generation is reachable twice.

    A design that reset or toggled the marker would let a token captured
    between two revocations come back to life.
    """
    first = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = auth.decode_token(first)

    assert (await _revoke(client, first)).status_code == 200
    second = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "rider@example.com", "password": PASSWORD},
        )
    ).json()["access_token"]
    assert (await _revoke(client, second)).status_code == 200
    assert await _generation(user_id) == 2

    for stale in (first, second):
        assert (await client.get(PROBE, headers=_headers(stale))).status_code == 401


@pytest.mark.asyncio
async def test_revoking_sessions_also_drops_trusted_devices(client, auth_headers):
    """A trusted device skips the second factor for thirty days.

    Pressing this button means a device is out of your hands, so leaving its
    cookie valid would revoke the session and keep the bypass that makes a new
    one cheap to get.
    """
    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = auth.decode_token(token)

    async with TestSessionLocal() as session:
        session.add(
            models.TrustedDevice(
                user_id=user_id,
                token_hash="a" * 64,
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                user_agent="pytest",
            )
        )
        await session.commit()

    assert (await _revoke(client, token)).status_code == 200

    async with TestSessionLocal() as session:
        remaining = (
            await session.execute(
                select(models.TrustedDevice).where(
                    models.TrustedDevice.user_id == user_id
                )
            )
        ).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_revoking_sessions_needs_a_session(client):
    response = await client.post(
        "/api/v1/auth/sessions/revoke", json={"password": PASSWORD}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# The claim itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_token_issued_before_this_feature_still_works(client, auth_headers):
    """Deploying the change must not sign everybody out.

    Old tokens carry no ``gen`` claim at all, which reads as 0 — the value the
    migration gives every existing row. They agree, so the token holds until
    the first revocation.
    """
    user_id = auth.decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    legacy = jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        auth.JWT_SECRET,
        algorithm=auth.JWT_ALGORITHM,
    )

    assert (await client.get(PROBE, headers=_headers(legacy))).status_code == 200

    assert (await _revoke(client, legacy)).status_code == 200
    assert (await client.get(PROBE, headers=_headers(legacy))).status_code == 401


@pytest.mark.asyncio
async def test_a_generation_the_account_never_reached_is_refused(client, auth_headers):
    """Mismatch is refused in both directions, not just "older than".

    This is what makes forgetting ``token_generation`` at a call site a visible
    failure rather than a token the revocation cannot reach.
    """
    user_id = auth.decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    ahead = auth.create_access_token(user_id, token_generation=99)

    assert (await client.get(PROBE, headers=_headers(ahead))).status_code == 401


@pytest.mark.asyncio
async def test_a_boolean_generation_claim_is_not_generation_one(client, auth_headers):
    """``True == 1`` in Python, so a bool claim would otherwise impersonate it.

    Tested against an account whose generation really is 1, because that is the
    only arrangement where the guard is load-bearing.
    """
    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = auth.decode_token(token)
    assert (await _revoke(client, token)).status_code == 200
    assert await _generation(user_id) == 1

    forged = jwt.encode(
        {
            "sub": user_id,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            auth.GENERATION_CLAIM: True,
        },
        auth.JWT_SECRET,
        algorithm=auth.JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException):
        auth.read_access_token(forged)
    assert (await client.get(PROBE, headers=_headers(forged))).status_code == 401


@pytest.mark.asyncio
async def test_a_non_numeric_generation_claim_is_refused(client, auth_headers):
    user_id = auth.decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    token = jwt.encode(
        {
            "sub": user_id,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            auth.GENERATION_CLAIM: "0",
        },
        auth.JWT_SECRET,
        algorithm=auth.JWT_ALGORITHM,
    )

    assert (await client.get(PROBE, headers=_headers(token))).status_code == 401


@pytest.mark.asyncio
async def test_every_issued_token_carries_the_accounts_generation(client, auth_headers):
    """Register, password login and the TOTP second step all stamp the token.

    Three call sites mint tokens, and one that passed the default would hand
    out a token its owner could not use after a revocation.
    """
    token = auth_headers["Authorization"].split(" ", 1)[1]
    user_id = auth.decode_token(token)
    assert auth.read_access_token(token).token_generation == 0

    assert (await _revoke(client, token)).status_code == 200

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": PASSWORD},
    )
    assert auth.read_access_token(login.json()["access_token"]).token_generation == 1
    assert await _generation(user_id) == 1


# ---------------------------------------------------------------------------
# The operator's lever
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_enabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "super-secret")


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/admin/login", json={"password": "super-secret"}
    )
    assert response.status_code == 200, response.text
    return _headers(response.json()["access_token"])


@pytest.mark.asyncio
async def test_an_operator_can_revoke_an_athletes_sessions(
    client, admin_enabled, auth_headers
):
    """The case the athlete's own button cannot cover: no password to hand."""
    admin = await _admin_headers(client)
    user_id = auth.decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    response = await client.post(
        f"/api/v1/admin/users/{user_id}/revoke-sessions", headers=admin
    )
    assert response.status_code == 200
    assert response.json()["tokenGeneration"] == 1
    assert (await client.get(PROBE, headers=auth_headers)).status_code == 401


@pytest.mark.asyncio
async def test_operator_revocation_needs_an_admin_token(
    client, admin_enabled, auth_headers
):
    user_id = auth.decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    anonymous = await client.post(f"/api/v1/admin/users/{user_id}/revoke-sessions")
    assert anonymous.status_code == 401

    # An athlete's own token must not reach it either — it is a valid JWT, just
    # not an admin one, which is the mistake worth testing for.
    as_athlete = await client.post(
        f"/api/v1/admin/users/{user_id}/revoke-sessions", headers=auth_headers
    )
    assert as_athlete.status_code == 403
    assert (await client.get(PROBE, headers=auth_headers)).status_code == 200


@pytest.mark.asyncio
async def test_operator_revocation_of_an_unknown_user_is_404(client, admin_enabled):
    admin = await _admin_headers(client)
    response = await client.post(
        "/api/v1/admin/users/does-not-exist/revoke-sessions", headers=admin
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Admin tokens, which have no row to count against
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotating_the_admin_password_invalidates_issued_admin_tokens(
    client, admin_enabled, monkeypatch
):
    """Rotating the password is what you do when a token leaked.

    Before this, the leaked token kept working for its full two hours — the
    rotation addressed the door and not the key already through it.
    """
    admin = await _admin_headers(client)
    assert (await client.get("/api/v1/admin/users", headers=admin)).status_code == 200

    monkeypatch.setattr(settings, "admin_password", "rotated-after-the-leak")

    response = await client.get("/api/v1/admin/users", headers=admin)
    assert response.status_code == 401
    assert response.json()["detail"] == auth.ADMIN_SESSION_STALE_DETAIL


@pytest.mark.asyncio
async def test_rotating_the_admin_totp_secret_invalidates_issued_admin_tokens(
    client, admin_enabled, monkeypatch
):
    admin = await _admin_headers(client)

    monkeypatch.setattr(settings, "admin_totp_secret", "JBSWY3DPEHPK3PXP")

    assert (await client.get("/api/v1/admin/users", headers=admin)).status_code == 401


@pytest.mark.asyncio
async def test_an_admin_token_without_the_credential_claim_is_refused(
    client, admin_enabled
):
    """Fail closed on a token that predates the binding.

    Admin tokens last two hours, so the worst this costs on deploy is one
    re-login — cheap enough that the safe direction is the obvious one.
    """
    token = jwt.encode(
        {
            "sub": "admin",
            "role": "admin",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        auth.JWT_SECRET,
        algorithm=auth.JWT_ALGORITHM,
    )

    response = await client.get("/api/v1/admin/users", headers=_headers(token))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_the_fingerprint_depends_on_both_credentials(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "a")
    monkeypatch.setattr(settings, "admin_totp_secret", "b")
    baseline = auth.admin_credential_fingerprint()

    monkeypatch.setattr(settings, "admin_password", "ab")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    # Concatenating the two would make these two configurations identical, and
    # rotating one of them a no-op.
    assert auth.admin_credential_fingerprint() != baseline
