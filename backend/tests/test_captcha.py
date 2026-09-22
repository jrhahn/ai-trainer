"""The registration proof-of-work (#686).

Two layers, tested apart: the scheme itself, and the route that enforces it.
The interesting cases are all the ways a *wrong* solution can be wrong, because
an accepted forgery makes the whole thing decorative.
"""

from __future__ import annotations

import hashlib

import pytest
from httpx import AsyncClient

from config import settings
from routers import dependencies as auth_limit_dep
from services import captcha as captcha_service

_CHALLENGE = "/api/v1/auth/captcha/challenge"
_REGISTER = "/api/v1/auth/register"


@pytest.fixture(autouse=True)
def _captcha_on(monkeypatch):
    """The suite turns the captcha off globally; this file is what tests it."""
    monkeypatch.setattr(settings, "captcha_enabled", True)
    captcha_service.reset_replay_guard()
    auth_limit_dep.captcha_limiter.reset()
    yield
    captcha_service.reset_replay_guard()
    auth_limit_dep.captcha_limiter.reset()


def _solve(challenge: dict) -> dict:
    """Do what the browser does: brute-force the number."""
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


@pytest.fixture
def small_challenge(monkeypatch):
    """Keep the brute force cheap so the suite stays fast."""
    monkeypatch.setattr(settings, "captcha_max_number", 200)


# ---------------------------------------------------------------------------
# The scheme
# ---------------------------------------------------------------------------


def test_a_correct_solution_is_accepted(small_challenge):
    issued = captcha_service.issue_challenge().as_dict()

    captcha_service.verify_solution(_solve(issued))


def test_a_wrong_number_is_rejected(small_challenge):
    issued = captcha_service.issue_challenge().as_dict()
    solution = _solve(issued)
    solution["number"] += 1

    with pytest.raises(captcha_service.CaptchaError, match="incorrect"):
        captcha_service.verify_solution(solution)


def test_a_forged_challenge_is_rejected(small_challenge):
    """The whole point of the signature: a client cannot invent its own puzzle.

    Here the attacker picks a salt and number they know, computes the matching
    digest, and submits a consistent triple. It is internally valid and must
    still be refused, because this server never issued it.
    """
    salt = "attacker.0"
    number = 7
    forged = {
        "challenge": hashlib.sha256(f"{salt}{number}".encode()).hexdigest(),
        "salt": salt,
        "signature": "0" * 64,
        "number": number,
    }

    with pytest.raises(captcha_service.CaptchaError, match="not issued"):
        captcha_service.verify_solution(forged)


def test_a_solution_cannot_be_replayed(small_challenge):
    """Otherwise the scheme is one puzzle solved once and reused forever."""
    issued = captcha_service.issue_challenge().as_dict()
    solution = _solve(issued)

    captcha_service.verify_solution(solution)
    with pytest.raises(captcha_service.CaptchaError, match="already used"):
        captcha_service.verify_solution(solution)


def test_an_expired_challenge_is_rejected(small_challenge, monkeypatch):
    monkeypatch.setattr(settings, "captcha_ttl_seconds", 60)
    issued = captcha_service.issue_challenge(now=1000.0).as_dict()
    solution = _solve(issued)

    with pytest.raises(captcha_service.CaptchaError, match="expired"):
        captcha_service.verify_solution(solution, now=1100.0)


def test_a_failed_attempt_does_not_burn_the_challenge(small_challenge):
    """An attacker must not be able to invalidate someone else's in-flight puzzle.

    The replay claim is made only after the solution is known to be good; if it
    happened earlier, submitting garbage against a known signature would be a
    denial-of-service on whoever is still filling in the form.
    """
    issued = captcha_service.issue_challenge().as_dict()
    solution = _solve(issued)
    wrong = {**solution, "number": solution["number"] + 1}

    with pytest.raises(captcha_service.CaptchaError):
        captcha_service.verify_solution(wrong)

    captcha_service.verify_solution(solution)


@pytest.mark.parametrize(
    "solution",
    [
        None,
        {},
        {"challenge": "a", "salt": "b", "signature": "c"},
        {"challenge": "a", "salt": "b", "signature": "c", "number": "5"},
        {"challenge": "a", "salt": "b", "signature": "c", "number": -1},
        {"challenge": "a", "salt": "b", "signature": "c", "number": True},
        {"challenge": "", "salt": "b", "signature": "c", "number": 1},
    ],
)
def test_malformed_solutions_are_rejected(solution):
    with pytest.raises(captcha_service.CaptchaError):
        captcha_service.verify_solution(solution)


def test_an_absurd_number_is_refused_before_hashing():
    """A bounded verifier: the work belongs on the client, not here."""
    with pytest.raises(captcha_service.CaptchaError, match="out of range"):
        captcha_service.verify_solution(
            {
                "challenge": "a" * 64,
                "salt": "s.0",
                "signature": "c",
                "number": 10**12,
            }
        )


def test_disabling_the_captcha_accepts_anything(monkeypatch):
    monkeypatch.setattr(settings, "captcha_enabled", False)

    captcha_service.verify_solution(None)


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_challenge_endpoint_returns_a_solvable_puzzle(
    client: AsyncClient, small_challenge
):
    resp = await client.get(_CHALLENGE)

    assert resp.status_code == 200
    body = resp.json()
    assert body["algorithm"] == "SHA-256"
    assert body["maxnumber"] == 200
    # Solvable, which is the only property that matters to a client.
    _solve(body)


@pytest.mark.asyncio
async def test_registration_without_a_captcha_is_refused(
    client: AsyncClient, small_challenge
):
    resp = await client.post(
        _REGISTER,
        json={"name": "Bot", "email": "bot@example.com", "password": "Str0ng!Pass"},
    )

    assert resp.status_code == 400
    assert "captcha" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_registration_with_a_solved_captcha_succeeds(
    client: AsyncClient, small_challenge
):
    issued = (await client.get(_CHALLENGE)).json()

    resp = await client.post(
        _REGISTER,
        json={
            "name": "Real Person",
            "email": "human@example.com",
            "password": "Str0ng!Pass",
            "captcha": _solve(issued),
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_one_solved_captcha_cannot_register_two_accounts(
    client: AsyncClient, small_challenge
):
    """The replay guard, end to end — this is what stops bulk signup."""
    issued = (await client.get(_CHALLENGE)).json()
    solution = _solve(issued)

    first = await client.post(
        _REGISTER,
        json={
            "name": "One",
            "email": "one@example.com",
            "password": "Str0ng!Pass",
            "captcha": solution,
        },
    )
    second = await client.post(
        _REGISTER,
        json={
            "name": "Two",
            "email": "two@example.com",
            "password": "Str0ng!Pass",
            "captcha": solution,
        },
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 400
    assert "already used" in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_the_challenge_endpoint_is_404_when_disabled(
    client: AsyncClient, monkeypatch
):
    """How the client learns to skip solving instead of burning CPU for nothing."""
    monkeypatch.setattr(settings, "captcha_enabled", False)

    resp = await client.get(_CHALLENGE)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_registration_still_works_with_the_captcha_disabled(
    client: AsyncClient, monkeypatch
):
    """A deployment that does not want this must not need a captcha field."""
    monkeypatch.setattr(settings, "captcha_enabled", False)

    resp = await client.post(
        _REGISTER,
        json={
            "name": "No Captcha",
            "email": "nocaptcha@example.com",
            "password": "Str0ng!Pass",
        },
    )

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_the_challenge_endpoint_is_rate_limited(
    client: AsyncClient, monkeypatch, small_challenge
):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "captcha_challenge_rate_limit_attempts", 3)

    statuses = [(await client.get(_CHALLENGE)).status_code for _ in range(4)]

    assert statuses[:3] == [200, 200, 200], statuses
    assert statuses[3] == 429, statuses
