"""Accounts: finding and creating users, and the tokens they spend."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

_USER_EAGER_OPTIONS = [
    selectinload(models.User.strava_token),
    selectinload(models.User.intervals_token),
    selectinload(models.User.rider_assessment),
]


async def get_user_by_id(db: AsyncSession, user_id: str) -> models.User | None:
    """Return a User with all relationships eagerly loaded, or None."""
    return await db.scalar(
        select(models.User)
        .options(*_USER_EAGER_OPTIONS)
        .where(models.User.id == user_id)
    )


async def get_user_by_email(db: AsyncSession, email: str) -> models.User | None:
    """Return a User by email with all relationships eagerly loaded, or None."""
    return await db.scalar(
        select(models.User)
        .options(*_USER_EAGER_OPTIONS)
        # Case-insensitive: emails differing only in case are one address
        # (ops#109). Covered by the lower(email) index the migration adds.
        .where(func.lower(models.User.email) == email.lower())
    )


async def get_user_by_email_simple(db: AsyncSession, email: str) -> models.User | None:
    """Return a User by email without loading relationships, or None."""
    return await db.scalar(
        select(models.User).where(func.lower(models.User.email) == email.lower())
    )


async def get_users_with_training_plans(db: AsyncSession) -> list[models.User]:
    """Return onboarded users that have a persisted training plan."""
    result = await db.scalars(
        select(models.User)
        .join(models.TrainingPlan)
        .options(
            *_USER_EAGER_OPTIONS,
            selectinload(models.User.training_plan),
        )
        .where(models.User.is_onboarded.is_(True))
        .order_by(models.User.id)
    )
    return list(result)


async def get_users_with_activity_sources(db: AsyncSession) -> list[models.User]:
    """Return onboarded users with a Strava or intervals.icu connection.

    The activity sync needs a connection, not a plan (ai-trainer-ops#279). An
    athlete whose first plan failed to generate is onboarded and connected, and
    their rides have to come in all the same; the plan jobs keep
    ``get_users_with_training_plans``.
    """
    result = await db.scalars(
        select(models.User)
        .options(
            *_USER_EAGER_OPTIONS,
            selectinload(models.User.training_plan),
        )
        .where(models.User.is_onboarded.is_(True))
        .where(or_(models.User.strava_token.has(), models.User.intervals_token.has()))
        .order_by(models.User.id)
    )
    return list(result)


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    name: str | None,
    hashed_password: str,
    verified: bool = True,
    privacy_consent: bool = False,
) -> models.User:
    """Create a new User, flush, and return the persisted instance.

    Confirmed unless ``verified`` is False: only a registration on a server that
    sends mail starts unconfirmed (app #687).
    """
    user = models.User(
        email=email,
        name=name,
        hashed_password=hashed_password,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
        privacy_consent_at=datetime.now(timezone.utc) if privacy_consent else None,
    )
    db.add(user)
    await db.flush()
    return user


async def increment_user_consumed_tokens(
    db: AsyncSession,
    user: models.User,
    tokens: int,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
) -> None:
    """Add provider-reported LLM tokens to a user's running usage counters.

    The split is kept alongside the total because input and output bill at very
    different rates and cached input at a tenth of the input rate, so the total
    alone cannot be converted into a cost (#516). ``cached_tokens`` is a subset
    of ``input_tokens``, not a fourth bucket.
    """
    if tokens <= 0 and input_tokens <= 0 and output_tokens <= 0:
        return
    user.consumed_tokens = int(user.consumed_tokens or 0) + max(0, int(tokens))
    user.consumed_input_tokens = int(user.consumed_input_tokens or 0) + max(
        0, int(input_tokens)
    )
    user.consumed_output_tokens = int(user.consumed_output_tokens or 0) + max(
        0, int(output_tokens)
    )
    user.consumed_cached_tokens = int(user.consumed_cached_tokens or 0) + max(
        0, int(cached_tokens)
    )
    await db.flush()


async def sum_user_tokens_since(
    db: AsyncSession,
    user_id: str,
    since: datetime,
) -> int:
    """Total provider tokens billed to *user_id* since *since*.

    Read from ``llm_calls`` rather than from ``users.consumed_tokens`` because
    the budget this answers is a rolling window and that column is a lifetime
    running total: windowing it would need either a reset job or a second pair
    of columns to reset, and both drift away from what was actually spent. The
    per-call table already carries the timestamp, so the window is free.

    Counts failed calls too — a rejected request still consumed the prompt, and
    a model that starts 400ing on every call must not become an unmetered way
    to spend money (#401).
    """
    result = await db.execute(
        select(func.coalesce(func.sum(models.LlmCall.total_tokens), 0)).where(
            models.LlmCall.user_id == user_id,
            models.LlmCall.created_at >= since,
        )
    )
    return int(result.scalar_one() or 0)


async def sum_admin_key_tokens_since(db: AsyncSession, since: datetime) -> int:
    """Total provider tokens since *since* that the owner's key likely paid for.

    Counts calls billed to nobody, and calls whose user holds no own key for
    the provider the call went to (an OpenAI key does not pay a Gemini call).
    """
    # ponytail: decided by the keys a user holds *now*, so a user who saved a
    # key today moves their earlier calls out of the sum; store the paying key
    # on llm_calls if that ever matters.
    result = await db.execute(
        select(func.coalesce(func.sum(models.LlmCall.total_tokens), 0))
        .select_from(models.LlmCall)
        .outerjoin(models.User, models.User.id == models.LlmCall.user_id)
        .where(
            models.LlmCall.created_at >= since,
            or_(
                models.LlmCall.user_id.is_(None),
                and_(
                    models.LlmCall.provider == "gemini",
                    models.User.user_gemini_api_key.is_(None),
                ),
                and_(
                    models.LlmCall.provider == "openai",
                    models.User.user_openai_api_key.is_(None),
                ),
            ),
        )
    )
    return int(result.scalar_one() or 0)


async def record_llm_calls(
    db: AsyncSession,
    user_id: str | None,
    source: str,
    records: Sequence[Any],
) -> None:
    """Store one row per provider call so the record outlives the container.

    The counters above answer "how much in total"; these rows answer "which
    feature, which model, which prompt, when" — the questions #516 built the
    log line for, which a deploy then threw away with the container (#549).

    *records* are ``services.token_accounting.CallRecord`` instances, taken
    structurally rather than by import so ``crud`` keeps knowing nothing about
    the accounting layer.
    """
    if not records:
        return
    db.add_all(
        [
            models.LlmCall(
                user_id=user_id,
                task=record.task[:30],
                provider=record.provider[:30],
                model=record.model[:80],
                source=source[:60],
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cached_tokens=record.cached_tokens,
                total_tokens=record.total_tokens,
                latency_ms=record.latency_ms,
                json_mode=record.json_mode,
                ok=record.ok,
                # Provider error strings carry the whole failed request in some
                # SDKs; the class name is what a cost query groups by.
                error=(record.error or None) and str(record.error)[:200],
                prompt_sha=record.prompt_sha[:12],
            )
            for record in records
        ]
    )
    await db.flush()
