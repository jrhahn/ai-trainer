"""Backend hardening (ops#132): unbounded inputs, 500s that should be 4xx.

Each verified case from the issue gets a test asserting the 4xx or the intended
behaviour. The Strava-token-leak and the AI-GET rate-limit cases live with their
existing suites (test_strava_extended.py, test_ai_rate_limit.py); the log-PII and
yaml.safe_dump changes are behaviour-preserving one-liners covered by inspection.
"""

import pytest

from services import dates


# --- Registration name: bounded and non-blank -----------------------------


@pytest.mark.parametrize("name", ["", "   ", "A" * 256])
async def test_register_rejects_bad_name(client, name):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": "badname@example.com", "password": "Str0ng!Pass"},
    )
    assert resp.status_code == 422, resp.text


async def test_register_still_accepts_a_valid_name(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Valid Rider", "email": "valid132@example.com", "password": "Str0ng!Pass"},
    )
    assert resp.status_code == 200, resp.text


# --- Chat question: bounded and non-blank ----------------------------------


@pytest.mark.parametrize("question", ["", "   ", "A" * 8001])
async def test_ask_trainer_rejects_bad_question(client, auth_headers, question):
    resp = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": question},
    )
    assert resp.status_code == 422, resp.text


# --- Plan save: a malformed day is a 422, not a 500 ------------------------


async def test_save_plan_rejects_malformed_day(client, auth_headers):
    """A day without the required ``date`` reached model_validate and 500'd."""
    resp = await client.put(
        "/api/v1/users/me/plan",
        headers=auth_headers,
        json={"plan": [{"workoutType": "rest"}]},
    )
    assert resp.status_code == 422, resp.text


# --- Timezone header: a bad value falls back to UTC, never 500 -------------


@pytest.mark.parametrize("bad", ["/etc/localtime", "../../etc/passwd", "Not/A/Zone"])
def test_app_timezone_falls_back_instead_of_raising(bad):
    """ZoneInfo raises ValueError (not ZoneInfoNotFoundError) on an absolute or
    relative path, which was an uncaught 500 on a crafted X-App-Timezone."""
    tz = dates.app_timezone(bad)
    assert tz is not None  # a usable ZoneInfo, not an exception
