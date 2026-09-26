"""The second factor (#688).

The cases worth having are the ones where a wrong answer is accepted, or a
right one is refused in a way that locks somebody out of their own account.
Both failure directions matter here: a second factor that can be walked around
is theatre, and one that cannot be recovered from is how people end up turning
it off.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from httpx import AsyncClient

import auth
import crud
import models
from config import settings
from routers import dependencies as limits
from services import totp as totp_service
from tests.conftest import TestSessionLocal

_LOGIN = "/api/v1/auth/login"
_LOGIN_TOTP = "/api/v1/auth/login/totp"
_ENROLL = "/api/v1/auth/totp/enroll"
_CONFIRM = "/api/v1/auth/totp/confirm"
_DISABLE = "/api/v1/auth/totp/disable"
_STATUS = "/api/v1/auth/totp/status"
_ADMIN_LOGIN = "/api/v1/admin/login"

_PASSWORD = "Str0ng!Pass"


@pytest.fixture(autouse=True)
def _reset():
    totp_service.reset_challenge_guard()
    limits.totp_code_limiter.reset()
    limits.login_limiter.reset()
    yield
    totp_service.reset_challenge_guard()
    limits.totp_code_limiter.reset()
    limits.login_limiter.reset()


async def _enrolled(client: AsyncClient, headers: dict) -> str:
    """Take an account all the way through enrollment; return its secret."""
    enroll = await client.post(_ENROLL, headers=headers)
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"].replace(" ", "")
    confirm = await client.post(
        _CONFIRM, headers=headers, json={"code": pyotp.TOTP(secret).now()}
    )
    assert confirm.status_code == 200, confirm.text
    return secret


# ---------------------------------------------------------------------------
# The scheme
# ---------------------------------------------------------------------------


def test_a_challenge_round_trips():
    token = totp_service.issue_challenge("user-1")

    assert totp_service.read_challenge(token) == "user-1"


def test_a_forged_challenge_is_rejected():
    """Without the signature a client could name any user it liked."""
    with pytest.raises(totp_service.TotpError):
        totp_service.read_challenge("victim.1700000000.nonce.deadbeef")


def test_a_challenge_is_single_use():
    """Otherwise one accepted password funds unlimited code attempts."""
    token = totp_service.issue_challenge("user-1")
    totp_service.read_challenge(token)

    with pytest.raises(totp_service.TotpError, match="already used"):
        totp_service.read_challenge(token)


def test_an_expired_challenge_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "totp_challenge_ttl_seconds", 60)
    token = totp_service.issue_challenge("user-1", now=1000.0)

    with pytest.raises(totp_service.TotpError, match="too long"):
        totp_service.read_challenge(token, now=1200.0)


def test_codes_from_a_different_secret_are_refused():
    secret, other = totp_service.new_secret(), totp_service.new_secret()

    assert totp_service.verify_code(secret, pyotp.TOTP(secret).now())
    assert not totp_service.verify_code(secret, pyotp.TOTP(other).now())


@pytest.mark.parametrize("code", ["", "   ", "abcdef", "12345a", None])
def test_malformed_codes_are_refused(code):
    secret = totp_service.new_secret()

    assert not totp_service.verify_code(secret, code)


def test_recovery_codes_are_distinct_and_normalise_consistently():
    codes = totp_service.new_recovery_codes()

    assert len(codes) == totp_service.RECOVERY_CODE_COUNT
    assert len(set(codes)) == len(codes)
    # Typed back with different spacing and case, a code must still match.
    assert totp_service.normalise_recovery_code(
        f"  {codes[0].upper().replace('-', ' - ')}  "
    ) == totp_service.normalise_recovery_code(codes[0])


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrolling_does_not_switch_it_on(client: AsyncClient, auth_headers):
    """A half-finished enrollment must not lock anybody out.

    Someone whose camera fails between these two calls is exactly the person
    least able to recover, so the secret is stored but the factor stays off
    until a code proves it works.
    """
    await client.post(_ENROLL, headers=auth_headers)

    status_resp = await client.get(_STATUS, headers=auth_headers)
    assert status_resp.json()["enabled"] is False

    login = await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    assert "accessToken" in login.json() or "access_token" in login.json()


@pytest.mark.asyncio
async def test_confirming_switches_it_on_and_returns_recovery_codes(
    client: AsyncClient, auth_headers
):
    enroll = await client.post(_ENROLL, headers=auth_headers)
    secret = enroll.json()["secret"].replace(" ", "")

    confirm = await client.post(
        _CONFIRM, headers=auth_headers, json={"code": pyotp.TOTP(secret).now()}
    )

    assert confirm.status_code == 200
    assert len(confirm.json()["recoveryCodes"]) == totp_service.RECOVERY_CODE_COUNT
    assert (await client.get(_STATUS, headers=auth_headers)).json()["enabled"] is True


@pytest.mark.asyncio
async def test_a_wrong_confirmation_code_leaves_it_off(client: AsyncClient, auth_headers):
    await client.post(_ENROLL, headers=auth_headers)

    confirm = await client.post(_CONFIRM, headers=auth_headers, json={"code": "000000"})

    assert confirm.status_code == 400
    assert (await client.get(_STATUS, headers=auth_headers)).json()["enabled"] is False


@pytest.mark.asyncio
async def test_re_enrolling_while_enabled_is_refused(client: AsyncClient, auth_headers):
    """Silently replacing the secret would break the app in use — and is what
    an attacker with a borrowed session would reach for."""
    await _enrolled(client, auth_headers)

    assert (await client.post(_ENROLL, headers=auth_headers)).status_code == 409


@pytest.mark.asyncio
async def test_recovery_codes_are_never_retrievable_again(
    client: AsyncClient, auth_headers
):
    confirm_codes = None
    enroll = await client.post(_ENROLL, headers=auth_headers)
    secret = enroll.json()["secret"].replace(" ", "")
    confirm = await client.post(
        _CONFIRM, headers=auth_headers, json={"code": pyotp.TOTP(secret).now()}
    )
    confirm_codes = confirm.json()["recoveryCodes"]

    status_resp = await client.get(_STATUS, headers=auth_headers)

    body = status_resp.text
    assert status_resp.json()["recoveryCodesRemaining"] == len(confirm_codes)
    for code in confirm_codes:
        assert code not in body


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_returns_a_challenge_instead_of_a_token(
    client: AsyncClient, auth_headers
):
    await _enrolled(client, auth_headers)

    login = await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})

    assert login.status_code == 200
    body = login.json()
    assert body["mfaRequired"] is True
    assert body["challenge"]
    # The whole point: no usable token came back from the password alone.
    assert "accessToken" not in body and "access_token" not in body


@pytest.mark.asyncio
async def test_the_second_step_returns_a_token(client: AsyncClient, auth_headers):
    secret = await _enrolled(client, auth_headers)
    challenge = (
        await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    ).json()["challenge"]

    resp = await client.post(
        _LOGIN_TOTP, json={"challenge": challenge, "code": pyotp.TOTP(secret).now()}
    )

    assert resp.status_code == 200
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_a_wrong_code_does_not_return_a_token(client: AsyncClient, auth_headers):
    await _enrolled(client, auth_headers)
    challenge = (
        await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    ).json()["challenge"]

    resp = await client.post(_LOGIN_TOTP, json={"challenge": challenge, "code": "000000"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_recovery_code_works_once(client: AsyncClient, auth_headers):
    enroll = await client.post(_ENROLL, headers=auth_headers)
    secret = enroll.json()["secret"].replace(" ", "")
    codes = (
        await client.post(_CONFIRM, headers=auth_headers, json={"code": pyotp.TOTP(secret).now()})
    ).json()["recoveryCodes"]

    async def _sign_in_with(code: str):
        challenge = (
            await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
        ).json()["challenge"]
        return await client.post(_LOGIN_TOTP, json={"challenge": challenge, "code": code})

    first = await _sign_in_with(codes[0])
    second = await _sign_in_with(codes[0])

    assert first.status_code == 200, first.text
    assert second.status_code == 401, "a recovery code must not be reusable"


@pytest.mark.asyncio
async def test_a_challenge_cannot_be_replayed_over_the_api(
    client: AsyncClient, auth_headers
):
    secret = await _enrolled(client, auth_headers)
    challenge = (
        await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    ).json()["challenge"]

    first = await client.post(
        _LOGIN_TOTP, json={"challenge": challenge, "code": pyotp.TOTP(secret).now()}
    )
    second = await client.post(
        _LOGIN_TOTP, json={"challenge": challenge, "code": pyotp.TOTP(secret).now()}
    )

    assert first.status_code == 200
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_code_guesses_are_rate_limited(
    client: AsyncClient, auth_headers, monkeypatch
):
    """Six digits is brute-forceable against an endpoint with no limit."""
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "totp_code_rate_limit_attempts", 3)
    await _enrolled(client, auth_headers)

    statuses = []
    for _ in range(4):
        challenge = (
            await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
        ).json()["challenge"]
        statuses.append(
            (
                await client.post(_LOGIN_TOTP, json={"challenge": challenge, "code": "000000"})
            ).status_code
        )

    assert statuses[-1] == 429, statuses


@pytest.mark.asyncio
async def test_a_user_without_totp_is_unaffected(client: AsyncClient, auth_headers):
    """Voluntary means voluntary — nobody gets a second step they didn't ask for."""
    login = await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})

    assert login.status_code == 200
    assert login.json()["access_token"]


# ---------------------------------------------------------------------------
# Trusted devices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_trusted_device_skips_the_second_step(client: AsyncClient, auth_headers):
    secret = await _enrolled(client, auth_headers)
    challenge = (
        await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    ).json()["challenge"]

    await client.post(
        _LOGIN_TOTP,
        json={
            "challenge": challenge,
            "code": pyotp.TOTP(secret).now(),
            "rememberDevice": True,
        },
    )

    # The client keeps the cookie, so this is the same browser coming back.
    again = await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    assert again.json().get("access_token"), again.text


@pytest.mark.asyncio
async def test_without_remember_device_the_second_step_still_applies(
    client: AsyncClient, auth_headers
):
    secret = await _enrolled(client, auth_headers)
    challenge = (
        await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    ).json()["challenge"]
    await client.post(
        _LOGIN_TOTP, json={"challenge": challenge, "code": pyotp.TOTP(secret).now()}
    )

    again = await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    assert again.json().get("mfaRequired") is True


@pytest.mark.asyncio
async def test_an_expired_device_does_not_skip_the_second_step(
    client: AsyncClient, auth_headers
):
    """The 30 days have to actually end."""
    secret = await _enrolled(client, auth_headers)
    challenge = (
        await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    ).json()["challenge"]
    await client.post(
        _LOGIN_TOTP,
        json={
            "challenge": challenge,
            "code": pyotp.TOTP(secret).now(),
            "rememberDevice": True,
        },
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email_simple(db, "rider@example.com")
        for device in await db.scalars(
            models.TrustedDevice.__table__.select().where(
                models.TrustedDevice.user_id == user.id
            )
        ):
            pass
        await db.execute(
            models.TrustedDevice.__table__.update().values(
                expires_at=datetime.now(timezone.utc) - timedelta(days=1)
            )
        )
        await db.commit()

    again = await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    assert again.json().get("mfaRequired") is True


@pytest.mark.asyncio
async def test_confirming_revokes_devices_trusted_before_it(
    client: AsyncClient, auth_headers
):
    """A device trusted while there was no second factor proves nothing about one."""
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email_simple(db, "rider@example.com")
        await crud.add_trusted_device(
            db,
            user.id,
            token_hash="stale",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            user_agent=None,
        )
        await db.commit()

    await _enrolled(client, auth_headers)

    status_resp = await client.get(_STATUS, headers=auth_headers)
    assert status_resp.json()["trustedDeviceCount"] == 0


# ---------------------------------------------------------------------------
# Disabling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabling_needs_the_password(client: AsyncClient, auth_headers):
    """A borrowed unlocked browser must not be able to strip the factor."""
    await _enrolled(client, auth_headers)

    resp = await client.post(_DISABLE, headers=auth_headers, json={"password": "wrong"})

    assert resp.status_code == 401
    assert (await client.get(_STATUS, headers=auth_headers)).json()["enabled"] is True


@pytest.mark.asyncio
async def test_disabling_clears_the_secret_and_the_codes(
    client: AsyncClient, auth_headers
):
    await _enrolled(client, auth_headers)

    resp = await client.post(_DISABLE, headers=auth_headers, json={"password": _PASSWORD})

    assert resp.status_code == 204
    status_body = (await client.get(_STATUS, headers=auth_headers)).json()
    assert status_body["enabled"] is False
    assert status_body["recoveryCodesRemaining"] == 0
    # And the account logs in with the password alone again.
    login = await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    assert login.json()["access_token"]


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_login_needs_a_code_when_configured(
    client: AsyncClient, monkeypatch
):
    secret = totp_service.new_secret()
    monkeypatch.setattr(settings, "admin_password", "super-secret")
    monkeypatch.setattr(settings, "admin_totp_secret", secret)

    without = await client.post(_ADMIN_LOGIN, json={"password": "super-secret"})
    wrong = await client.post(
        _ADMIN_LOGIN, json={"password": "super-secret", "code": "000000"}
    )
    right = await client.post(
        _ADMIN_LOGIN, json={"password": "super-secret", "code": pyotp.TOTP(secret).now()}
    )

    assert without.status_code == 401
    assert wrong.status_code == 401
    assert right.status_code == 200, right.text
    assert right.json()["access_token"]


@pytest.mark.asyncio
async def test_a_correct_code_does_not_rescue_a_wrong_password(
    client: AsyncClient, monkeypatch
):
    secret = totp_service.new_secret()
    monkeypatch.setattr(settings, "admin_password", "super-secret")
    monkeypatch.setattr(settings, "admin_totp_secret", secret)

    resp = await client.post(
        _ADMIN_LOGIN, json={"password": "nope", "code": pyotp.TOTP(secret).now()}
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_login_is_unchanged_without_a_secret(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "admin_password", "super-secret")
    monkeypatch.setattr(settings, "admin_totp_secret", "")

    resp = await client.post(_ADMIN_LOGIN, json={"password": "super-secret"})

    assert resp.status_code == 200
    assert (await client.get("/api/v1/admin/totp-required")).json()["required"] is False


@pytest.mark.asyncio
async def test_the_device_cookie_is_httponly_and_secure_in_production(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The flags are the protection; losing one silently would not show up above.

    ``httponly`` matters more here than for the JWT, which lives in
    sessionStorage by design: this value outlives the tab, so an XSS able to
    read it would keep bypassing the second factor for a month. ``secure``
    follows the environment because a browser discards a Secure cookie sent
    over http, and development is http — but it must be on in production.
    """
    monkeypatch.setattr(settings, "app_env", "production")
    secret = await _enrolled(client, auth_headers)
    challenge = (
        await client.post(_LOGIN, json={"email": "rider@example.com", "password": _PASSWORD})
    ).json()["challenge"]

    resp = await client.post(
        _LOGIN_TOTP,
        json={
            "challenge": challenge,
            "code": pyotp.TOTP(secret).now(),
            "rememberDevice": True,
        },
    )

    cookie = resp.headers.get("set-cookie", "")
    assert "httponly" in cookie.lower(), cookie
    assert "secure" in cookie.lower(), cookie
    assert "samesite=lax" in cookie.lower(), cookie


@pytest.mark.asyncio
async def test_a_malformed_admin_secret_says_so_instead_of_failing_silently(
    client: AsyncClient, monkeypatch, caplog
):
    """A typo in ADMIN_TOTP_SECRET was a silent lockout.

    A non-empty value makes the panel start demanding a code, while a
    non-base32 one can never be accepted — so the operator got "Invalid
    password or code" forever with nothing anywhere explaining why. Fail-closed
    is right; fail-closed and silent is the shape of #684 and #696.
    """
    monkeypatch.setattr(settings, "admin_password", "super-secret")
    monkeypatch.setattr(settings, "admin_totp_secret", "this-is-not-base32!!")

    with caplog.at_level("ERROR"):
        resp = await client.post(
            _ADMIN_LOGIN, json={"password": "super-secret", "code": "123456"}
        )

    assert resp.status_code == 401
    assert any("not valid base32" in r.getMessage() for r in caplog.records), (
        "the refusal has to be explained somewhere the operator will see it"
    )


def test_the_boot_check_reports_a_malformed_admin_secret(monkeypatch, caplog):
    monkeypatch.setattr(settings, "admin_totp_secret", "nope!!")

    with caplog.at_level("ERROR"):
        totp_service.warn_if_admin_secret_unusable()

    assert any("not valid base32" in r.getMessage() for r in caplog.records)


def test_the_boot_check_is_quiet_when_there_is_no_admin_secret(monkeypatch, caplog):
    """An unconfigured second factor is a valid state, not a misconfiguration."""
    monkeypatch.setattr(settings, "admin_totp_secret", "")

    with caplog.at_level("ERROR"):
        totp_service.warn_if_admin_secret_unusable()

    assert caplog.records == []


def test_a_hostile_email_cannot_inject_markup_into_the_qr():
    """The QR is inserted with dangerouslySetInnerHTML, so this has to hold.

    The account name comes from user input. It is encoded into QR *geometry*
    rather than into markup, so the SVG contains only paths — but that is a
    property worth pinning rather than assuming, since the render call could be
    swapped for one that embeds text.
    """
    hostile = '"><script>alert(1)</script>@example.com'
    svg = totp_service.qr_svg(
        totp_service.provisioning_uri(totp_service.new_secret(), account=hostile)
    )

    assert "script" not in svg.lower()
    assert "alert" not in svg
    assert hostile not in svg
    assert set(re.findall(r"<\s*([a-zA-Z0-9:_-]+)", svg)) <= {"svg", "path"}
