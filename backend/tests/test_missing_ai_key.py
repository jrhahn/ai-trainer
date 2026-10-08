"""A missing AI key is a 402 the athlete can act on, never a 500
(ai-trainer-ops#41).

The failure this suite exists for was invisible from the browser. With
admin-key fallback *on* but no global key configured — the state a deployment
is in before anyone sets ``GEMINI_API_KEY`` — ``_get_provider_global`` logged a
warning and returned a ``GeminiProvider`` **with no key**. The SDK then raised
``ValueError("No API key was provided")`` from inside ``genai.Client``, which is
not an ``AIKeyNotConfiguredError``, so it escaped every handler.

That is worse than an ordinary 500. An exception raised past the handlers is
caught by Starlette's ``ServerErrorMiddleware``, which sits *outside*
``CORSMiddleware``, so the response carries no CORS headers at all — and the
browser then reports "Failed to fetch" without ever exposing the status code to
the client. The athlete is told nothing, the client cannot distinguish it from
the network being down, and the one explanation is a log line only the operator
sees.

Why these tests do not mock ``get_provider``
--------------------------------------------
Because the test that already existed does, and that is how this got through.
``test_ai_endpoint_returns_402_when_no_key_and_fallback_disabled`` patches
``services.ai_service.get_provider`` to raise, so it proves the *handler* turns
the error into a 402 and says nothing about whether anything raises it. Here the
keys are cleared and the real resolution runs, so the condition is what is under
test.
"""

from __future__ import annotations

import pytest

from config import settings
from services import llm as llm_service


@pytest.fixture
def no_keys_anywhere(monkeypatch):
    """A deployment nobody has configured yet, with fallback left on.

    The default state of a fresh install: ``ALLOW_ADMIN_AI_KEY_FALLBACK``
    defaults to true, and neither provider key is set.
    """
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "allow_admin_ai_key_fallback", True)


# ---------------------------------------------------------------------------
# Through the route, which is where it mattered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_key_anywhere_answers_402_and_not_500(
    client, auth_headers, no_keys_anywhere
):
    """The second failure mode from the issue, end to end.

    Nothing in ``llm`` is patched: the request resolves a provider for real and
    has to arrive at 402 on its own.
    """
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "What should I ride today?"},
    )

    assert response.status_code == 402, response.text
    assert "API key" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_402_carries_cors_headers_the_browser_can_read(
    client, auth_headers, no_keys_anywhere
):
    """The half that made the old behaviour opaque rather than merely wrong.

    A handled 402 passes back out through ``CORSMiddleware`` and keeps its
    ``access-control-allow-origin``; a 500 from an unhandled exception does not,
    which is why the browser said "Failed to fetch" instead of showing a status.
    Asserting the header, not the absence of a crash, because the header is what
    decides whether the client can see the status at all.
    """
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers={**auth_headers, "Origin": "http://localhost:5173"},
        json={"question": "What should I ride today?"},
    )

    assert response.status_code == 402, response.text
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


# ---------------------------------------------------------------------------
# The resolution itself
# ---------------------------------------------------------------------------


def test_the_global_resolver_raises_instead_of_building_a_keyless_provider(
    no_keys_anywhere,
):
    with pytest.raises(llm_service.AIKeyNotConfiguredError, match="API key"):
        llm_service._get_provider_global("gemini", llm_service.TASK_COACH)


def test_the_error_names_the_provider_that_was_asked_for(no_keys_anywhere):
    """So an operator reading it learns which variable is empty.

    ``resolve_user_provider`` deliberately returns the user's stated preference
    when nothing is usable, precisely so this message can name it (#448). That
    only works if the message actually uses the argument.
    """
    with pytest.raises(llm_service.AIKeyNotConfiguredError, match="openai"):
        llm_service._get_provider_global("openai", llm_service.TASK_COACH)


def test_the_message_tells_the_athlete_something_true(no_keys_anywhere):
    """It says to add a key in Settings, so that has to work from here.

    With fallback on and no global key, a user's own key is still preferred over
    the global one, so the advice is actionable rather than a dead end. Pinned
    because a message that lies is worse than a bare 402: the athlete follows it
    and arrives nowhere.
    """
    token = llm_service.set_user_ai_keys({"gemini": "a-user-supplied-key"})
    try:
        provider = llm_service.get_provider("gemini", task=llm_service.TASK_COACH)
    finally:
        llm_service.reset_user_ai_keys(token)

    assert isinstance(provider, llm_service.GeminiProvider)


# ---------------------------------------------------------------------------
# Anti-vacuity: the suite must still see a configured deployment work
# ---------------------------------------------------------------------------


def test_a_configured_deployment_still_gets_a_provider(monkeypatch):
    """Every assertion above passes trivially if resolution raised always."""
    monkeypatch.setattr(settings, "gemini_api_key", "a-global-key")
    monkeypatch.setattr(settings, "openai_api_key", "")

    provider = llm_service._get_provider_global("gemini", llm_service.TASK_COACH)
    assert isinstance(provider, llm_service.GeminiProvider)


def test_one_configured_provider_still_serves_a_request_for_the_other(monkeypatch):
    """The pre-existing cross-provider fallback must survive the change.

    Asking for openai on a deployment that only has a Gemini key returns the
    Gemini provider rather than raising — the behaviour the four branches above
    the new raise exist for, and the thing a careless fix would have removed.
    """
    monkeypatch.setattr(settings, "gemini_api_key", "a-global-key")
    monkeypatch.setattr(settings, "openai_api_key", "")

    provider = llm_service._get_provider_global("openai", llm_service.TASK_COACH)
    assert isinstance(provider, llm_service.GeminiProvider)
