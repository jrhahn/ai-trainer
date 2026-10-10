"""Stay signed in: the HttpOnly session cookie and its CSRF token (ai-trainer-ops#45).

The properties under test: the web app's session is never readable by script,
it authenticates on its own, a state-changing request it authenticates needs
the CSRF token, and every way of ending a session still ends it.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import auth
from main import app

PASSWORD = "Str0ng!Pass"
COOKIE_MODE = {"X-Auth-Mode": "cookie"}
PROBE = "/api/v1/auth/totp/status"


@pytest_asyncio.fixture
async def browser():
    # https, so a Secure cookie is sent back the way a browser would.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as ac:
        yield ac


async def _sign_up(browser: AsyncClient) -> str:
    response = await browser.post(
        "/api/v1/auth/register",
        json={"name": "Rider", "email": "rider@example.com", "password": PASSWORD},
        headers=COOKIE_MODE,
    )
    assert response.status_code == 200, response.text
    return response.json()["csrfToken"]


def _session_cookie_header(response) -> str:
    return next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{auth.SESSION_COOKIE}=")
    )


async def test_the_body_carries_no_token_and_the_cookie_is_httponly(browser):
    response = await browser.post(
        "/api/v1/auth/register",
        json={"name": "Rider", "email": "rider@example.com", "password": PASSWORD},
        headers=COOKIE_MODE,
    )
    assert "access_token" not in response.json()
    cookie = _session_cookie_header(response).lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert f"max-age={auth.SESSION_DAYS * 24 * 3600}" in cookie


async def test_without_the_header_a_client_still_gets_a_bearer_token(browser):
    response = await browser.post(
        "/api/v1/auth/register",
        json={"name": "Rider", "email": "rider@example.com", "password": PASSWORD},
    )
    assert response.json()["access_token"]
    assert auth.SESSION_COOKIE not in browser.cookies


async def test_password_sign_in_starts_a_cookie_session(browser):
    await _sign_up(browser)
    browser.cookies.clear()
    response = await browser.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": PASSWORD},
        headers=COOKIE_MODE,
    )
    assert response.json()["csrfToken"]
    assert (await browser.get(PROBE)).status_code == 200


async def test_the_cookie_authenticates_a_read(browser):
    await _sign_up(browser)
    assert (await browser.get(PROBE)).status_code == 200


async def test_a_write_without_the_csrf_token_is_refused(browser):
    await _sign_up(browser)
    response = await browser.post(
        "/api/v1/auth/sessions/revoke", json={"password": PASSWORD}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == auth.CSRF_REJECTED_DETAIL


async def test_a_write_with_a_wrong_csrf_token_is_refused(browser):
    await _sign_up(browser)
    response = await browser.post(
        "/api/v1/auth/sessions/revoke",
        json={"password": PASSWORD},
        headers={auth.CSRF_HEADER: "0" * 32},
    )
    assert response.status_code == 403


async def test_sign_out_everywhere_ends_the_cookie_session(browser):
    csrf = await _sign_up(browser)
    response = await browser.post(
        "/api/v1/auth/sessions/revoke",
        json={"password": PASSWORD},
        headers={auth.CSRF_HEADER: csrf},
    )
    assert response.status_code == 200, response.text
    probe = await browser.get(PROBE)
    assert probe.status_code == 401
    assert probe.json()["detail"] == auth.SESSION_REVOKED_DETAIL


async def test_resume_renews_the_cookie_and_hands_back_the_csrf_token(browser):
    await _sign_up(browser)
    response = await browser.get("/api/v1/auth/resume")
    assert response.status_code == 200
    claims = auth.read_access_token(browser.cookies[auth.SESSION_COOKIE])
    assert response.json()["csrfToken"] == auth.csrf_token_for(claims.session_id)
    assert _session_cookie_header(response)


async def test_resume_without_a_cookie_is_signed_out_quietly(browser):
    # 204, not 401: every signed-out visitor asks, and none of them is an error.
    assert (await browser.get("/api/v1/auth/resume")).status_code == 204


async def test_resume_clears_a_cookie_that_no_longer_authenticates(browser):
    csrf = await _sign_up(browser)
    await browser.post(
        "/api/v1/auth/sessions/revoke",
        json={"password": PASSWORD},
        headers={auth.CSRF_HEADER: csrf},
    )
    response = await browser.get("/api/v1/auth/resume")
    assert response.status_code == 204
    assert auth.SESSION_COOKIE not in browser.cookies


async def test_resume_refuses_a_bearer_token(browser):
    token = (
        await browser.post(
            "/api/v1/auth/register",
            json={"name": "Rider", "email": "rider@example.com", "password": PASSWORD},
        )
    ).json()["access_token"]
    response = await browser.get(
        "/api/v1/auth/resume", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 204
    assert auth.SESSION_COOKIE not in response.headers.get("set-cookie", "")


async def test_logout_clears_the_cookie(browser):
    await _sign_up(browser)
    response = await browser.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert auth.SESSION_COOKIE not in browser.cookies
    assert (await browser.get(PROBE)).status_code == 401


async def test_a_bearer_header_wins_over_the_cookie_and_needs_no_csrf(browser):
    # A request that carries the header was not forged cross-site.
    await _sign_up(browser)
    other = (
        await browser.post(
            "/api/v1/auth/register",
            json={"name": "Other", "email": "other@example.com", "password": PASSWORD},
        )
    ).json()["access_token"]
    response = await browser.post(
        "/api/v1/auth/sessions/revoke",
        json={"password": PASSWORD},
        headers={"Authorization": f"Bearer {other}"},
    )
    assert response.status_code == 200
    # The cookie's account was not the one revoked.
    assert (await browser.get(PROBE)).status_code == 200


async def test_a_renewal_in_another_tab_leaves_this_tab_able_to_write(
    browser, monkeypatch
):
    # Tab A signed in and holds its CSRF token; tab B opens and resumes, which
    # replaces the cookie for both. A's next write must still go through.
    # Found in review on PR #808.
    tab_a_csrf = await _sign_up(browser)
    first = browser.cookies[auth.SESSION_COOKIE]
    # A renewal within the same second would mint the identical JWT and hide
    # the bug; a different expiry makes the new cookie really new.
    monkeypatch.setattr(auth, "SESSION_DAYS", auth.SESSION_DAYS - 1)
    tab_b = await browser.get("/api/v1/auth/resume")
    assert browser.cookies[auth.SESSION_COOKIE] != first
    assert tab_b.json()["csrfToken"] == tab_a_csrf

    response = await browser.post(
        "/api/v1/auth/sessions/revoke",
        json={"password": PASSWORD},
        headers={auth.CSRF_HEADER: tab_a_csrf},
    )
    assert response.status_code == 200, response.text


async def test_a_new_sign_in_gets_a_new_csrf_token(browser):
    first = await _sign_up(browser)
    second = (
        await browser.post(
            "/api/v1/auth/login",
            json={"email": "rider@example.com", "password": PASSWORD},
            headers=COOKIE_MODE,
        )
    ).json()["csrfToken"]
    assert second != first


async def test_a_cookie_without_a_session_id_cannot_write(browser):
    # A bearer token planted as the cookie was never a cookie session, so there
    # is no CSRF token that matches it.
    token = (
        await browser.post(
            "/api/v1/auth/register",
            json={"name": "Rider", "email": "rider@example.com", "password": PASSWORD},
        )
    ).json()["access_token"]
    response = await browser.post(
        "/api/v1/auth/sessions/revoke",
        json={"password": PASSWORD},
        headers={"Cookie": f"{auth.SESSION_COOKIE}={token}", auth.CSRF_HEADER: ""},
    )
    assert response.status_code == 403
