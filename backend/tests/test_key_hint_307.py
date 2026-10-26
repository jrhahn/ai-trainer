"""Settings shows a saved key masked, and behind the eye only its hint (ai-trainer-ops#307).

The hint is the key's first and last four characters. No response carries more
of a key than that: the athlete's AI keys and the intervals.icu key alike.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

import models
from routers import intervals as intervals_router
from services.key_hint import key_hint
from tests.conftest import TestSessionLocal

# Shaped like the real ones: Gemini 39 characters, OpenAI more than 40,
# intervals.icu 25.
GEMINI_KEY = "AIzaSyQx7Lm2Vt9Rk4Wp8Zs3Jd6Hf1Nc5Bg0Tue"
OPENAI_KEY = "sk-proj-Xb4Nq8Lr2Mz6Kc1Vw9Hy3Ft7Gd5Sj0Pe4Ua8Ri2Oy6Tq1Wn3"
INTERVALS_KEY = "7q2w9e4r1t8y3u6i0o5p2a7sd"


def _shows_at_most_the_hint(body: str, key: str) -> bool:
    """No five characters of *key* in a row appear in *body*; the hint shows four."""
    return not any(key[i : i + 5] in body for i in range(len(key) - 4))


@pytest.fixture(autouse=True)
def _intervals_accepts_keys(monkeypatch):
    """Saving an intervals.icu key asks intervals.icu first; not here."""

    async def listing(*_args, **_kwargs):
        return [], 0

    monkeypatch.setattr(intervals_router, "fetch_activity_listing", listing)


def test_the_hint_is_the_first_and_last_four_characters():
    assert len(GEMINI_KEY) == 39
    assert key_hint(GEMINI_KEY) == "AIza…0Tue"
    assert key_hint(INTERVALS_KEY) == "7q2w…a7sd"


def test_a_key_too_short_to_hint_at_gets_no_hint():
    """Four and four of a short key would be most of it: twenty characters at least."""
    assert key_hint("k" * 16 + "9876") == "kkkk…9876"
    assert key_hint("k" * 15 + "9876") is None
    assert key_hint("sk-super-secret") is None


@pytest.mark.parametrize("missing", [None, ""])
def test_no_key_has_no_hint(missing):
    assert key_hint(missing) is None


@pytest.mark.asyncio
async def test_the_status_names_each_saved_key_by_its_hint(
    client: AsyncClient, auth_headers
):
    saved = await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "gemini", "apiKey": GEMINI_KEY},
    )
    assert saved.json()["geminiKeyHint"] == "AIza…0Tue"
    assert saved.json()["openaiKeyHint"] is None

    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "openai", "apiKey": OPENAI_KEY},
    )
    status = await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)

    assert status.json()["geminiKeyHint"] == "AIza…0Tue"
    assert status.json()["openaiKeyHint"] == "sk-p…1Wn3"


@pytest.mark.asyncio
async def test_a_removed_key_leaves_no_hint(client: AsyncClient, auth_headers):
    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "gemini", "apiKey": GEMINI_KEY},
    )
    await client.delete("/api/v1/users/me/ai-key?provider=gemini", headers=auth_headers)

    status = await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)
    assert status.json()["geminiKeyHint"] is None


@pytest.mark.asyncio
async def test_a_key_saved_before_the_hint_existed_has_one(
    client: AsyncClient, auth_headers
):
    """The hint is cut from the stored key, so no key needs a pass or a new save."""
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    async with TestSessionLocal() as db:
        user = await db.get(models.User, me.json()["id"])
        user.user_gemini_api_key = GEMINI_KEY
        db.add(
            models.IntervalsToken(
                user_id=user.id, api_key=INTERVALS_KEY, athlete_id="0"
            )
        )
        await db.commit()

    status = await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)
    connection = await client.get("/api/v1/intervals/connection", headers=auth_headers)
    profile = await client.get("/api/v1/users/me", headers=auth_headers)

    assert status.json()["geminiKeyHint"] == "AIza…0Tue"
    assert connection.json()["keyHint"] == "7q2w…a7sd"
    assert profile.json()["intervalsConnection"]["keyHint"] == "7q2w…a7sd"


@pytest.mark.asyncio
async def test_no_response_carries_more_of_a_key_than_its_hint(
    client: AsyncClient, auth_headers
):
    """The done-when of ai-trainer-ops#307: every response that speaks of a
    saved key, after each key is saved."""
    bodies = []
    for provider, key in (("gemini", GEMINI_KEY), ("openai", OPENAI_KEY)):
        saved = await client.put(
            "/api/v1/users/me/ai-key",
            headers=auth_headers,
            json={"provider": provider, "apiKey": key},
        )
        assert saved.status_code == 200
        bodies.append(saved.text)
    connected = await client.put(
        "/api/v1/intervals/connection",
        headers=auth_headers,
        json={"apiKey": INTERVALS_KEY, "athleteId": "0"},
    )
    assert connected.status_code == 200
    bodies.append(connected.text)
    for path in (
        "/api/v1/users/me/ai-key/status",
        "/api/v1/intervals/connection",
        "/api/v1/users/me",
    ):
        response = await client.get(path, headers=auth_headers)
        assert response.status_code == 200, path
        bodies.append(response.text)

    everything = "\n".join(bodies)
    for key in (GEMINI_KEY, OPENAI_KEY, INTERVALS_KEY):
        assert key_hint(key) in everything
        assert _shows_at_most_the_hint(everything, key), key
