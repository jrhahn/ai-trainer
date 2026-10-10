"""Print LLM cost per active user as Markdown (ai-trainer-ops#8).

Run against a deployment's database, from ``backend/``:

    uv run python -m scripts.cost_report [--days 30]

Reads the ``llm_calls`` rows (#549) of the last ``--days`` days. An *active
user* is one with at least one attributed call in that window. The answer the
issue asks for — what one athlete costs per month, split by the scheduled
analysis steps, per-ride review and chat, and the most expensive athlete next
to the median — is the first table.

Prints aggregates only: counts, tokens and dollars, nothing about any athlete.

What it cannot see:

* **Whose key paid.** A call made on an athlete's own provider key is stored
  like any other, so it is counted here as if the operator paid for it.
* **Prices for other models.** Only the models in :data:`PRICE_PER_MILLION`
  are priced; tokens on anything else are listed separately rather than
  guessed at.
* **What triggered a step.** A ``step:`` source names the unit of work, not
  whether the scheduler or an endpoint ran it, so "scheduled analysis" means
  "the steps the scheduler runs", whoever ran them this time.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select

import models
from database import async_session_maker

# USD per million tokens, (input, output). The coach model's list price, the
# same figures `services/token_accounting` and the Grafana spend panel use.
# Cached tokens are a subset of input and get no discount here: flash-lite
# offers no context caching (#538).
PRICE_PER_MILLION: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
}

SCHEDULED = "scheduled analysis"
RIDE_REVIEW = "per-ride review"
CHAT = "chat"
OTHER = "other"
BUCKETS = (SCHEDULED, RIDE_REVIEW, CHAT, OTHER)

# Sources the issue's three buckets are made of, read off the call sites. The
# scheduled steps are the ones a `ScheduledJob` runs; ride review is the sync
# jobs (whose per-ride chain bills to them) plus the endpoints that review or
# rate a ride. Everything else lands in "other" and is still itemised below.
_BUCKET_OF_SOURCE = {
    "step:hypothesis-generation": SCHEDULED,
    "step:experiment-suggestion": SCHEDULED,
    "step:prediction-evaluation": SCHEDULED,
    "step:insight-generation": SCHEDULED,
    "step:athlete-model-derivation": SCHEDULED,
    "step:contradiction-detection": SCHEDULED,
    "step:open-question-generation": SCHEDULED,
    "step:plan-maintenance": SCHEDULED,
    "job:strava-sync": RIDE_REVIEW,
    "job:intervals-sync": RIDE_REVIEW,
    "api:review-new-rides": RIDE_REVIEW,
    "api:process-pending-feedbacks": RIDE_REVIEW,
    "api:rate-workout": RIDE_REVIEW,
    "api:resolve-ride-match": RIDE_REVIEW,
    "api:analyse-fit-import": RIDE_REVIEW,
    "api:ask-trainer": CHAT,
    "bg:update-coach-memory": CHAT,
}


@dataclass(frozen=True, slots=True)
class Usage:
    user_id: str | None
    source: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int


def bucket_of(source: str) -> str:
    return _BUCKET_OF_SOURCE.get(source, OTHER)


def cost_of(usage: Usage) -> float | None:
    """Dollars for this usage, or ``None`` for a model with no known price."""
    price = PRICE_PER_MILLION.get(usage.model)
    if price is None:
        return None
    return (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / 1e6


def render_markdown(usages: Iterable[Usage], days: int) -> str:
    usages = list(usages)
    per_user: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(BUCKETS, 0.0))
    per_source: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0, 0.0])
    unpriced: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    unattributed = 0.0
    for usage in usages:
        cost = cost_of(usage)
        row = per_source[usage.source]
        row[0] += usage.calls
        row[1] += usage.input_tokens
        row[2] += usage.output_tokens
        if cost is None:
            unpriced[usage.model][0] += usage.input_tokens
            unpriced[usage.model][1] += usage.output_tokens
            continue
        row[3] += cost
        if usage.user_id is None:
            unattributed += cost
        else:
            per_user[usage.user_id][bucket_of(usage.source)] += cost

    lines = [f"## LLM cost per active user, last {days} days", ""]
    if not per_user:
        lines.append("_No priced, attributed calls in this window._")
    else:
        totals = {user: sum(split.values()) for user, split in per_user.items()}
        top = max(totals, key=totals.__getitem__)
        n = len(totals)
        lines += [
            f"{n} active users. Median **${statistics.median(totals.values()):.4f}**, "
            f"mean ${sum(totals.values()) / n:.4f}, "
            f"most expensive **${totals[top]:.4f}**.",
            "",
            "| | mean per active user | most expensive user |",
            "|---|---|---|",
        ]
        for bucket in BUCKETS:
            mean = sum(split[bucket] for split in per_user.values()) / n
            lines.append(f"| {bucket} | ${mean:.4f} | ${per_user[top][bucket]:.4f} |")
        lines.append(f"| **total** | ${sum(totals.values()) / n:.4f} | ${totals[top]:.4f} |")
    lines += ["", f"Calls billed to no user (`user_id` empty): ${unattributed:.4f}."]
    if unpriced:
        lines += ["", "Not priced, model has no entry in `PRICE_PER_MILLION`:", ""]
        for model, (tokens_in, tokens_out) in sorted(unpriced.items()):
            lines.append(f"- `{model}`: {tokens_in:,} input, {tokens_out:,} output tokens")
    if per_source:
        lines += [
            "",
            "### By source",
            "",
            "| source | bucket | calls | input tokens | output tokens | cost |",
            "|---|---|---|---|---|---|",
        ]
        for source, (calls, tokens_in, tokens_out, cost) in sorted(
            per_source.items(), key=lambda item: -item[1][3]
        ):
            lines.append(
                f"| `{source}` | {bucket_of(source)} | {calls} | {tokens_in:,} "
                f"| {tokens_out:,} | ${cost:.4f} |"
            )
    return "\n".join(lines)


async def build(days: int, now: datetime | None = None) -> str:
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    call = models.LlmCall
    query = (
        select(
            call.user_id,
            call.source,
            call.model,
            func.count(),
            func.sum(call.input_tokens),
            func.sum(call.output_tokens),
        )
        .where(call.created_at >= since)
        .group_by(call.user_id, call.source, call.model)
    )
    async with async_session_maker() as session:
        rows = (await session.execute(query)).all()
    return render_markdown((Usage(*row) for row in rows), days)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30, help="window length (default 30)")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")
    print(asyncio.run(build(args.days)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
