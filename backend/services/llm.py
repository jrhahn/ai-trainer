"""LLM provider abstraction.

Adds a thin Protocol layer so the rest of the codebase never branches on
provider strings beyond this module.  New providers (e.g. Anthropic) only
require implementing the ``LLMProvider`` protocol here.

``AIRateLimitError`` is defined here and re-exported from
``services.ai_service`` for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from config import settings

logger = logging.getLogger(__name__)

OPENAI_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-2.5-flash"


class AIRateLimitError(Exception):
    """Raised when the AI provider returns a rate-limit (429) response."""


@runtime_checkable
class LLMProvider(Protocol):
    async def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...
    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str: ...


class OpenAIProvider:
    def __init__(self) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        return response.choices[0].message.content or ""

    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, *messages],
            **kwargs,
        )
        return response.choices[0].message.content or ""


class GeminiProvider:
    async def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        from google import genai
        from google.genai import errors as genai_errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if json_mode else None,
        )
        try:
            client = genai.Client(api_key=settings.gemini_api_key)
            async with client.aio as aio_client:
                response = await aio_client.models.generate_content(
                    model=GEMINI_MODEL, contents=user, config=config
                )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise AIRateLimitError(str(exc)) from exc
            raise
        return response.text or ""

    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str:
        from google import genai
        from google.genai import errors as genai_errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if json_mode else None,
        )
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]
        try:
            client = genai.Client(api_key=settings.gemini_api_key)
            async with client.aio as aio_client:
                response = await aio_client.models.generate_content(
                    model=GEMINI_MODEL, contents=contents, config=config
                )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise AIRateLimitError(str(exc)) from exc
            raise
        return response.text or ""


def get_provider(name: str) -> LLMProvider:
    """Return an ``LLMProvider`` for *name*.

    Falls back to the best available provider when the requested one is not
    configured.
    """
    if name == "gemini" and settings.gemini_api_key:
        return GeminiProvider()
    if name == "openai" and settings.openai_api_key:
        return OpenAIProvider()
    # Fallback: use whichever key is present
    if settings.gemini_api_key:
        return GeminiProvider()
    if settings.openai_api_key:
        return OpenAIProvider()
    logger.warning("No AI provider API key configured; defaulting to GeminiProvider")
    return GeminiProvider()
