"""Measure whether Gemini's implicit cache actually fires for the coach prompt.

Production reported ``cached=0`` on every coach call, including two consecutive
calls whose system prompts were byte-identical (#538). Two very different
things produce that number and only one of them is fixable by us:

1. our prefix is not stable — ruled out offline by ``test_coach_prompt_cache``;
2. the model does not serve an implicit cache hit for this request shape — not
   knowable from our logs, and the reason this script exists.

It sends the *real* coach prefix (``prompts.coach_static_prefix``) several
times in a row and prints what the API reports, for each placement:

``system``
    the prefix as ``system_instruction``, exactly what the app does today.
``content``
    the prefix as the first user turn instead. Google's guidance is to "put
    large and common contents at the beginning of your prompt", which is
    ambiguous about system instructions; this is the A/B that settles it.

The first call of each run populates; a hit, if it comes, shows on the second.

Usage:
    cd backend
    python scripts/probe_implicit_cache.py
    python scripts/probe_implicit_cache.py --models gemini-3.5-flash-lite,gemini-3.5-flash
    python scripts/probe_implicit_cache.py --placement content --repeats 4

Requires GEMINI_API_KEY. Costs real money: repeats × placements × models calls
of ~5,000 input tokens each, which is a few cents at the flash-lite rate. Output
is capped at 16 tokens because only the usage metadata is of interest.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from services.llm import _ZERO_THINKING_BUDGET_UNSUPPORTED  # noqa: E402
from services.prompts import coach_static_prefix  # noqa: E402

# Enough of a question to get an answer, short enough not to matter.
QUESTION = "In one sentence: should I ride easy today?"


async def _one_call(model: str, prefix: str, placement: str) -> dict[str, int]:
    from google import genai
    from google.genai import types

    kwargs: dict = {"max_output_tokens": 16}
    if placement == "system":
        kwargs["system_instruction"] = prefix
        contents: object = QUESTION
    else:
        contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=prefix)]),
            types.Content(role="user", parts=[types.Part.from_text(text=QUESTION)]),
        ]
    # Mirrors GeminiProvider._build_config so the request shape matches the app.
    if model not in _ZERO_THINKING_BUDGET_UNSUPPORTED:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

    client = genai.Client(api_key=settings.gemini_api_key)
    async with client.aio as aio:
        response = await aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**kwargs),
        )
    usage = response.usage_metadata
    return {
        "input": getattr(usage, "prompt_token_count", 0) or 0,
        "cached": getattr(usage, "cached_content_token_count", 0) or 0,
        "output": getattr(usage, "candidates_token_count", 0) or 0,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        default=settings.gemini_coach_model,
        help="comma-separated model names (default: the configured coach model)",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--placement", choices=("system", "content", "both"), default="both"
    )
    args = parser.parse_args()

    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 1

    prefix = coach_static_prefix()
    placements = (
        ("system", "content") if args.placement == "both" else (args.placement,)
    )
    print(f"coach prefix: {len(prefix)} chars (~{len(prefix) // 4} tokens)\n")
    print(f"{'model':<26} {'placement':<10} {'call':>4} {'input':>7} {'cached':>7} {'hit':>6}")

    any_hit = False
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        for placement in placements:
            for call in range(1, args.repeats + 1):
                try:
                    usage = await _one_call(model, prefix, placement)
                except Exception as exc:  # noqa: BLE001 — a probe reports, never raises
                    print(f"{model:<26} {placement:<10} {call:>4}  failed: {exc}")
                    continue
                share = (
                    100 * usage["cached"] / usage["input"] if usage["input"] else 0.0
                )
                any_hit = any_hit or usage["cached"] > 0
                print(
                    f"{model:<26} {placement:<10} {call:>4} {usage['input']:>7} "
                    f"{usage['cached']:>7} {share:>5.1f}%"
                )

    print()
    if any_hit:
        print("Implicit caching DOES fire — a zero in production is our prompt's fault.")
    else:
        print(
            "No hit on any repeat. The prefix is stable (see test_coach_prompt_cache), "
            "so reordering the prompt cannot help; the options are explicit caching, "
            "a model that serves implicit hits, or a smaller prompt."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
