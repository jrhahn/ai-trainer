"""The chat history, and the flags an athlete sets on a wrong reply."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models


# ---------------------------------------------------------------------------
# ChatMessage
# ---------------------------------------------------------------------------


async def get_chat_messages(
    db: AsyncSession, user_id: str, *, limit: int | None = None
) -> list[models.ChatMessage]:
    """Return a user's ChatMessages in persisted conversation order.

    With *limit*, only the newest *limit* of them, still oldest first (#284):
    the coach reads its last ten, and every chat message used to load the
    whole history to get them.
    """
    order = (
        models.ChatMessage.timestamp,
        models.ChatMessage.created_at,
        models.ChatMessage.id,
    )
    query = select(models.ChatMessage).where(models.ChatMessage.user_id == user_id)
    if limit is None:
        return list(await db.scalars(query.order_by(*order)))
    newest = await db.scalars(
        query.order_by(*(column.desc() for column in order)).limit(limit)
    )
    return list(reversed(list(newest)))


async def get_athlete_messages_since(
    db: AsyncSession, user_id: str, since: datetime, *, limit: int = 12
) -> list[tuple[datetime, str]]:
    """The athlete's own chat messages since ``since``, oldest first, at most
    ``limit`` of the newest (ai-trainer-ops#152). Each with the time it was
    written, so a run can read "tomorrow" from the day it was said."""
    rows = (
        await db.execute(
            select(models.ChatMessage.created_at, models.ChatMessage.content)
            .where(
                models.ChatMessage.user_id == user_id,
                models.ChatMessage.role == "user",
                models.ChatMessage.created_at >= since,
            )
            .order_by(
                models.ChatMessage.created_at.desc(), models.ChatMessage.id.desc()
            )
            .limit(limit)
        )
    ).all()
    return [(created_at, content) for created_at, content in reversed(rows)]


async def get_recent_athlete_messages(
    db: AsyncSession, user_id: str, *, limit: int = 3
) -> list[str]:
    """Return the athlete's own last ``limit`` chat messages, oldest first (#658).

    Only ``role="user"`` rows — assistant text is what we are trying to fix, so
    feeding it back as a language sample would just entrench English. Used by the
    plan narrator, which has no athlete turn of its own to read the language from.
    """
    result = await db.scalars(
        select(models.ChatMessage.content)
        .where(
            models.ChatMessage.user_id == user_id,
            models.ChatMessage.role == "user",
        )
        .order_by(
            models.ChatMessage.timestamp.desc(),
            models.ChatMessage.created_at.desc(),
            models.ChatMessage.id.desc(),
        )
        .limit(limit)
    )
    return [content for content in reversed(list(result)) if content]


async def create_chat_message(
    db: AsyncSession,
    user_id: str,
    *,
    role: str,
    content: str,
    timestamp: str,
    plan_update_count: int | None = None,
    flagged_constraint_dates: list[str] | None = None,
    notes: list[dict] | None = None,
) -> models.ChatMessage:
    """Create a ChatMessage, flush, and return the persisted instance."""
    message = models.ChatMessage(
        user_id=user_id,
        role=role,
        content=content,
        timestamp=timestamp,
        plan_update_count=plan_update_count,
        flagged_constraint_dates=flagged_constraint_dates or None,
        notes_json=notes or None,
    )
    db.add(message)
    await db.flush()
    return message


async def count_wrong_flags_since(
    db: AsyncSession, user_id: str, since: datetime
) -> int:
    """How many "this is wrong" flags *user_id* sent since *since* (ai-trainer-ops#94)."""
    count = await db.scalar(
        select(func.count())
        .select_from(models.WrongFlag)
        .where(
            models.WrongFlag.user_id == user_id, models.WrongFlag.created_at >= since
        )
    )
    return count or 0


async def create_wrong_flag(
    db: AsyncSession,
    user_id: str,
    *,
    target: str,
    ref: str,
    excerpt: str,
    note: str | None,
    git_sha: str,
) -> models.WrongFlag:
    flag = models.WrongFlag(
        user_id=user_id,
        target=target,
        ref=ref,
        excerpt=excerpt,
        note=note,
        git_sha=git_sha,
    )
    db.add(flag)
    await db.flush()
    return flag


async def list_wrong_flags(
    db: AsyncSession, limit: int = 200
) -> list[tuple[models.WrongFlag, str]]:
    """The newest flags with the flagging account's email, for the admin page."""
    rows = await db.execute(
        select(models.WrongFlag, models.User.email)
        .join(models.User, models.User.id == models.WrongFlag.user_id)
        .order_by(models.WrongFlag.created_at.desc())
        .limit(limit)
    )
    return [(flag, email) for flag, email in rows.all()]


async def set_wrong_flag_handled(
    db: AsyncSession, flag_id: str, *, handled: bool, note: str | None
) -> models.WrongFlag | None:
    """Mark a flag handled with the owner's note, or open again (ai-trainer-ops#96)."""
    flag = await db.get(models.WrongFlag, flag_id)
    if flag is None:
        return None
    flag.handled_at = datetime.now(timezone.utc) if handled else None
    flag.handled_note = note if handled else None
    await db.flush()
    return flag


async def wrong_flag_weekly_counts(
    db: AsyncSession, weeks: int, now: datetime | None = None
) -> list[tuple[date, int]]:
    """Flags per rolling week, oldest first: (the week's first day, count)."""
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(weeks=weeks)
    created = (
        await db.scalars(
            select(models.WrongFlag.created_at).where(
                models.WrongFlag.created_at >= start
            )
        )
    ).all()
    counts = [0] * weeks
    for when in created:
        # SQLite hands back naive datetimes; the column is UTC either way.
        aware = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        index = min(int((aware - start) / timedelta(weeks=1)), weeks - 1)
        counts[index] += 1
    return [((start + timedelta(weeks=i)).date(), counts[i]) for i in range(weeks)]


async def delete_chat_messages(db: AsyncSession, user_id: str) -> None:
    """Delete all ChatMessages for a user."""
    await db.execute(
        delete(models.ChatMessage).where(models.ChatMessage.user_id == user_id)
    )
