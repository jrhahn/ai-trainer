"""Tests for bring-your-own-key (BYOK) AI provider credential management.

Covers:
  - GET /users/me/ai-key/status — key presence reporting
  - PUT /users/me/ai-key        — save / replace key
  - DELETE /users/me/ai-key     — delete key
  - POST /users/me/ai-key/test  — validate key without storing
  - LLM routing: user key used when BYOK context is active
  - 402 returned when no key configured and admin fallback disabled
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from config import settings
from services import llm as llm_service


# ---------------------------------------------------------------------------
# GET /users/me/ai-key/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_key_status_defaults_no_keys(client, auth_headers):
    response = await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["hasOpenaiKey"] is False
    assert data["hasGeminiKey"] is False
    assert data["provider"] in ("openai", "gemini")


# ---------------------------------------------------------------------------
# PUT /users/me/ai-key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_openai_key(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "openai", "apiKey": "sk-test-1234"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["hasOpenaiKey"] is True
    assert data["hasGeminiKey"] is False
    assert data["provider"] == "openai"


@pytest.mark.asyncio
async def test_save_gemini_key(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "gemini", "apiKey": "AIza-test-key"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["hasGeminiKey"] is True
    assert data["provider"] == "gemini"


@pytest.mark.asyncio
async def test_save_key_updates_ai_provider(client, auth_headers):
    """Saving a key for a provider also switches the user's active provider."""
    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "gemini", "apiKey": "AIza-abc"},
    )
    status_response = await client.get(
        "/api/v1/users/me/ai-key/status", headers=auth_headers
    )
    assert status_response.json()["provider"] == "gemini"


@pytest.mark.asyncio
async def test_save_key_unknown_provider_returns_400(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "anthropic", "apiKey": "sk-ant-test"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_key_not_returned_in_status(client, auth_headers):
    """The stored key is never returned in the status response."""
    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "openai", "apiKey": "sk-super-secret"},
    )
    status = await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)
    body = status.text
    assert "sk-super-secret" not in body


# ---------------------------------------------------------------------------
# DELETE /users/me/ai-key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_openai_key(client, auth_headers):
    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "openai", "apiKey": "sk-test"},
    )
    del_response = await client.delete(
        "/api/v1/users/me/ai-key?provider=openai", headers=auth_headers
    )
    assert del_response.status_code == 204

    status = await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)
    assert status.json()["hasOpenaiKey"] is False


@pytest.mark.asyncio
async def test_delete_gemini_key(client, auth_headers):
    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "gemini", "apiKey": "AIza-test"},
    )
    del_response = await client.delete(
        "/api/v1/users/me/ai-key?provider=gemini", headers=auth_headers
    )
    assert del_response.status_code == 204

    status = await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)
    assert status.json()["hasGeminiKey"] is False


@pytest.mark.asyncio
async def test_delete_key_unknown_provider_returns_400(client, auth_headers):
    response = await client.delete(
        "/api/v1/users/me/ai-key?provider=anthropic", headers=auth_headers
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /users/me/ai-key/test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_key_endpoint_succeeds_with_valid_key(client, auth_headers):
    """A key that produces a successful chat response → {"ok": true}."""
    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value="ok")

    with patch.object(llm_service, "get_provider", return_value=mock_provider):
        response = await client.post(
            "/api/v1/users/me/ai-key/test",
            headers=auth_headers,
            json={"provider": "openai", "apiKey": "sk-valid"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_test_key_endpoint_fails_with_bad_key(client, auth_headers):
    """A key that causes an exception → 422."""
    with patch.object(
        llm_service, "get_provider", side_effect=Exception("Invalid API key")
    ):
        response = await client.post(
            "/api/v1/users/me/ai-key/test",
            headers=auth_headers,
            json={"provider": "openai", "apiKey": "sk-invalid"},
        )

    assert response.status_code == 422
    assert "Key validation failed" in response.json()["detail"]


# ---------------------------------------------------------------------------
# LLM routing: user key is passed to get_provider via ContextVar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_key_is_active_in_byok_context(client, auth_headers):
    """When the user has a key set, the BYOK ContextVar contains that key during AI requests."""
    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "openai", "apiKey": "sk-user-key-abc"},
    )

    captured_keys: list[dict] = []

    def capturing_get_provider(name: str, task: str = llm_service.TASK_COACH):
        ctx = llm_service._user_ai_keys.get()
        if ctx is not llm_service._BYOK_INACTIVE and isinstance(ctx, dict):
            captured_keys.append(dict(ctx))
        # Return a mock provider that doesn't make real network calls
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value='{"category": "general", "needs_science_rag": false}')
        provider.chat_history = AsyncMock(
            return_value='{"response": "ok", "plan_updates": [], "newFacts": [], "memory_updated": false, "context_updated": false, "exchangeId": "x"}'
        )
        return provider

    with patch("services.ai_service.get_provider", side_effect=capturing_get_provider):
        await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "How am I doing?"},
        )

    assert any(k.get("openai") == "sk-user-key-abc" for k in captured_keys), (
        f"Expected 'sk-user-key-abc' in captured keys but got: {captured_keys}"
    )


# ---------------------------------------------------------------------------
# 402 when no key configured and admin fallback disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_endpoint_returns_402_when_no_key_and_fallback_disabled(
    client, auth_headers
):
    """With allow_admin_ai_key_fallback=False and no user key → 402."""
    # Patch the local reference in ai_service (where it's imported directly)
    with patch(
        "services.ai_service.get_provider",
        side_effect=llm_service.AIKeyNotConfiguredError(
            "No openai API key configured. Please add your key in Settings → AI Provider."
        ),
    ):
        response = await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "Test question"},
        )

    assert response.status_code == 402
    assert "API key" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Admin fallback: user key takes priority over global key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_key_takes_priority_over_global_settings(monkeypatch):
    """When a user has their own key, it is used even if global settings has one."""
    from config import settings

    monkeypatch.setattr(settings, "openai_api_key", "global-key")

    keys = {"openai": "user-specific-key", "gemini": None}
    token = llm_service.set_user_ai_keys(keys)
    try:
        provider = llm_service.get_provider("openai", task=llm_service.TASK_CLASSIFY)
        # The provider should have been constructed with the user's key
        assert provider._client.api_key == "user-specific-key"
    finally:
        llm_service.reset_user_ai_keys(token)


@pytest.mark.asyncio
async def test_key_status_reports_the_models_the_backend_actually_runs(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The UI must not have to guess, and must not be able to drift (#691).

    Both settings components hard-coded these as display strings, disagreeing
    with each other ("Gemini 2.0 Flash" vs "Gemini 2.5 Flash") and with the
    backend, which has run gemini-3.5-flash-lite since #511. A copy of a config
    value drifts from it; serving the value cannot.
    """
    monkeypatch.setattr(settings, "gemini_coach_model", "gemini-3.5-flash-lite")
    monkeypatch.setattr(settings, "openai_coach_model", "gpt-4o")

    resp = await client.get("/api/v1/users/me/ai-key/status", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["geminiModel"] == "gemini-3.5-flash-lite"
    assert body["openaiModel"] == "gpt-4o"


@pytest.mark.asyncio
async def test_saving_a_key_also_reports_the_models(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The save path returns the same shape as the read path.

    Both build ``AIKeyStatusSchema`` by hand, so a field added to one and
    forgotten in the other is a live possibility — the UI reads whichever
    response happens to arrive last.
    """
    monkeypatch.setattr(settings, "gemini_coach_model", "gemini-3.5-flash-lite")

    resp = await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "gemini", "apiKey": "AIza-test-key"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["geminiModel"] == "gemini-3.5-flash-lite"


# ---------------------------------------------------------------------------
# The .fit uploads spend tokens outside the /ai router (#693)
# ---------------------------------------------------------------------------


def _fake_parsed_fit():
    """A minimally valid parse result, so the request reaches the AI call.

    A bogus payload makes ``_parse_fit_activity`` raise before anything
    LLM-shaped runs, which would make these tests pass for the wrong reason.
    """
    from routers.users import _ParsedFitActivity

    return _ParsedFitActivity(
        source_id=987654321,
        source_metadata={},
        sport_type="cycling",
        duration_seconds=3600,
        duration_minutes=60,
        ride_date="2026-09-20",
        completed_at="2026-09-20T09:00:00+00:00",
        activity_name="Test ride",
        avg_power=200,
        avg_hr=140,
        start_lat=None,
        start_lng=None,
        streams={},
    )


@pytest.mark.asyncio
async def test_upload_fit_runs_inside_the_byok_context(client, auth_headers):
    """The upload routes must put the athlete's keys in scope, like /ai does.

    Without the dependency, ``get_provider`` finds no context and takes the
    branch written for scheduler jobs: the global key, used unconditionally and
    without consulting ``allow_admin_ai_key_fallback``. So a deployment that
    had switched to BYOK-only kept billing the owner for every .fit import —
    invisibly, because nothing errors on that path.

    Asserting the *context* rather than the outcome is deliberate: the outcome
    depends on which key happens to be configured, the context is the wiring
    that was missing.
    """
    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "gemini", "apiKey": "AIza-user-own-key"},
    )

    seen: list[dict] = []

    def capture(*_args, **_kwargs):
        ctx = llm_service._user_ai_keys.get()
        if ctx is not llm_service._BYOK_INACTIVE and isinstance(ctx, dict):
            seen.append(dict(ctx))
        raise llm_service.AIKeyNotConfiguredError("stop here — the context is the point")

    with (
        patch("routers.users._parse_fit_activity", return_value=_fake_parsed_fit()),
        patch("services.ai_service.get_provider", side_effect=capture),
    ):
        await client.post(
            "/api/v1/users/me/upload-fit",
            headers=auth_headers,
            files={"file": ("ride.fit", b"not a real fit file", "application/octet-stream")},
        )

    assert any(k.get("gemini") == "AIza-user-own-key" for k in seen), (
        "upload-fit ran outside the BYOK context, so it would spend the global "
        f"key regardless of the fallback setting. Captured: {seen}"
    )


@pytest.mark.asyncio
async def test_bulk_upload_fit_runs_inside_the_byok_context(client, auth_headers):
    """Same for the bulk route — it was the one missed in #682 as well."""
    await client.put(
        "/api/v1/users/me/ai-key",
        headers=auth_headers,
        json={"provider": "gemini", "apiKey": "AIza-user-own-key"},
    )

    seen: list[dict] = []

    def capture(*_args, **_kwargs):
        ctx = llm_service._user_ai_keys.get()
        if ctx is not llm_service._BYOK_INACTIVE and isinstance(ctx, dict):
            seen.append(dict(ctx))
        raise llm_service.AIKeyNotConfiguredError("stop here — the context is the point")

    with (
        patch("routers.users._parse_fit_activity", return_value=_fake_parsed_fit()),
        patch("services.ai_service.get_provider", side_effect=capture),
    ):
        await client.post(
            "/api/v1/users/me/upload-fit/bulk",
            headers=auth_headers,
            files=[("files", ("ride.fit", b"not a real fit file", "application/octet-stream"))],
        )

    assert any(k.get("gemini") == "AIza-user-own-key" for k in seen), (
        f"upload-fit/bulk ran outside the BYOK context. Captured: {seen}"
    )
