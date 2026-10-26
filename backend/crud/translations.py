"""The stored translations of plan texts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import models


async def get_plan_text_translations(
    db: AsyncSession, user_id: str, language: str, source_hashes: Sequence[str]
) -> dict[str, str]:
    """Stored plan-text translations by the English text's hash (ai-trainer-ops#121)."""
    if not source_hashes:
        return {}
    rows = await db.scalars(
        select(models.PlanTextTranslation).where(
            models.PlanTextTranslation.user_id == user_id,
            models.PlanTextTranslation.language == language,
            models.PlanTextTranslation.source_hash.in_(list(source_hashes)),
        )
    )
    return {row.source_hash: row.text for row in rows}


async def save_plan_text_translations(
    db: AsyncSession, user_id: str, language: str, by_hash: dict[str, str]
) -> None:
    """Store new translations. A hash already stored keeps its text: the same
    English text means the same thing.

    Each row goes in under its own savepoint. Two requests can both read a hash
    as missing (another worker, or a second tab after the first one's read), and
    the second insert then hits the unique constraint. That request's text is
    dropped rather than failing the request: the stored one stands.
    """
    known = await get_plan_text_translations(db, user_id, language, list(by_hash))
    now = datetime.now(timezone.utc)
    for source_hash, text in by_hash.items():
        if source_hash in known:
            continue
        try:
            async with db.begin_nested():
                db.add(
                    models.PlanTextTranslation(
                        user_id=user_id,
                        language=language,
                        source_hash=source_hash,
                        text=text,
                        created_at=now,
                    )
                )
                await db.flush()
        except IntegrityError:
            continue
