"""Signup allowance: a wave fits, and a bad captcha costs no slot (ops#108).

The global registration limit used to be spent before the captcha and before
the existence check: five per hour for everyone together, so an invite group's
sixth friend was refused, and five anonymous POSTs with no captcha could block
every signup. The fix verifies the captcha first (so a request with no solved
challenge never reaches the allowance) and raises the default; the allowance is
still spent before the existence check, so a duplicate 409 keeps costing a slot
and the address-taken oracle stays bounded (test_auth_hardening pins that).
"""

import hashlib

import pytest
from httpx import AsyncClient

from config import settings
from routers import dependencies as deps
from services import captcha as captcha_service

_REGISTER = "/api/v1/auth/register"
_STRONG = "Str0ng-passphrase!"


def _solve(challenge: dict) -> dict:
    """Brute-force the challenge number, as the browser does."""
    salt = challenge["salt"]
    for number in range(challenge["maxnumber"] + 1):
        if hashlib.sha256(f"{salt}{number}".encode()).hexdigest() == challenge["challenge"]:
            return {
                "challenge": challenge["challenge"],
                "salt": salt,
                "signature": challenge["signature"],
                "number": number,
            }
    raise AssertionError("challenge had no solution in range")


@pytest.mark.asyncio
async def test_eight_signups_within_the_window_all_succeed(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    deps.registration_limiter.reset()
    # The raised default must cover an invite wave.
    assert settings.registration_rate_limit_attempts >= 8

    statuses = [
        (
            await client.post(
                _REGISTER,
                json={
                    "name": f"Rider {i}",
                    "email": f"wave-{i}@example.com",
                    "password": _STRONG,
                },
            )
        ).status_code
        for i in range(8)
    ]
    deps.registration_limiter.reset()

    assert statuses == [200] * 8, statuses


@pytest.mark.asyncio
async def test_requests_without_a_valid_captcha_do_not_reduce_the_allowance(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "captcha_enabled", True)
    monkeypatch.setattr(settings, "captcha_max_number", 200)
    monkeypatch.setattr(settings, "registration_rate_limit_attempts", 2)
    monkeypatch.setattr(settings, "registration_rate_limit_seconds", 3600)
    deps.registration_limiter.reset()
    deps.captcha_limiter.reset()
    captcha_service.reset_replay_guard()

    # Five requests with no captcha: each a 400, and none may spend a slot.
    for _ in range(5):
        resp = await client.post(
            _REGISTER,
            json={"name": "R", "email": "probe@example.com", "password": _STRONG},
        )
        assert resp.status_code == 400, resp.text

    # The two slots are therefore still free: two captcha-proven signups succeed.
    for i in range(2):
        challenge = captcha_service.issue_challenge().as_dict()
        resp = await client.post(
            _REGISTER,
            json={
                "name": "R",
                "email": f"real-{i}@example.com",
                "password": _STRONG,
                "captcha": _solve(challenge),
            },
        )
        assert resp.status_code == 200, resp.text
    deps.registration_limiter.reset()
