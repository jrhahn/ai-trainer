"""Narrate automated coach plan changes to the athlete (#439).

The plan pipeline records *what* an automated run changed (``PlanDayHistory``
rows sharing a ``batch_id``). This module adds the *why*: one LLM call turns a
run's applied changes into a first-person summary plus a per-day reason, then
persists them and posts a single coach chat message. It is deliberately
fail-safe — narration must never break the plan write that preceded it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services import ai_service
from services.llm import resolve_user_provider
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# Automated triggers whose plan changes earn a one-message narrative. User-driven
# triggers (``user_edit``, ``coach_chat``, ``next_ride``) already surface in the
# UI, and a full ``generate`` is an initial build rather than an adjustment to
# explain — see services/plan_pipeline.PLAN_SOURCES.
NARRATED_SOURCES = frozenset(
    {"nightly_maintenance", "adapt", "auto_adapt", "ride_review"}
)

# How the coach frames each run when addressing the athlete.
_RUN_CONTEXT = {
    "nightly_maintenance": "your daily overnight routine check of the plan",
    "adapt": "an automatic refresh of your plan after it had gone stale",
    "auto_adapt": "an automatic adjustment after your latest activity synced",
    "ride_review": "a review of your plan after your most recent ride",
}


async def narrate_plan_changes(
    db: AsyncSession,
    user: models.User,
    *,
    batch_id: str | None,
    source: str,
    applied_changes: list[dict],
    profile: dict | None = None,
    rider_assessment: dict | None = None,
    training_load_section: str = "",
    weather_context_section: str = "",
) -> models.PlanChangeSummary | None:
    """Summarise one automated coach run and post a single coach chat message.

    No-op unless ``source`` is narrated, a ``batch_id`` was recorded, and at least
    one change was actually applied (a blocked-only run changed nothing the
    athlete should hear about). Persists a ``PlanChangeSummary``, backfills each
    changed day's ``reason``, and creates exactly one assistant ``ChatMessage``.
    Any failure is swallowed and logged.
    """
    if source not in NARRATED_SOURCES or not batch_id or not applied_changes:
        return None
    try:
        if profile is None:
            profile = schemas.UserProfileSchema.from_user(user).model_dump(
                by_alias=True
            )
        # Only read the relationship if it is already loaded — touching an
        # unloaded relationship inside async SQLAlchemy raises. Callers that have
        # it eagerly loaded (nightly maintenance) get a personalised assessment;
        # others simply omit it.
        if (
            rider_assessment is None
            and "rider_assessment" not in sa_inspect(user).unloaded
            and user.rider_assessment is not None
        ):
            rider_assessment = schemas.RiderAssessmentSchema.model_validate(
                user.rider_assessment, from_attributes=True
            ).model_dump(by_alias=True)
        provider = resolve_user_provider(user)
        async with track_llm_usage(db, user, source="coach-narration"):
            parsed = await ai_service.summarize_plan_changes(
                applied_changes,
                profile,
                run_context=_RUN_CONTEXT.get(source, "an automatic plan adjustment"),
                provider=provider,
                rider_assessment=rider_assessment,
                training_load_section=training_load_section,
                weather_context_section=weather_context_section,
            )

        summary = (parsed.get("summary") or "").strip()
        if not summary:
            logger.warning(
                "Coach narration produced no summary for batch_id=%s; skipping",
                batch_id,
            )
            return None

        reasons = {
            entry["date"]: entry["reason"].strip()
            for entry in parsed.get("days") or []
            if isinstance(entry, dict)
            and entry.get("date")
            and (entry.get("reason") or "").strip()
        }

        row = await crud.create_plan_change_summary(
            db, user.id, batch_id=batch_id, source=source, summary=summary
        )
        if reasons:
            await crud.set_plan_day_reasons(db, batch_id, reasons)
        await crud.create_chat_message(
            db,
            user.id,
            role="assistant",
            content=summary,
            timestamp=datetime.now(timezone.utc).isoformat(),
            plan_update_count=len(applied_changes),
        )
        return row
    except Exception:
        logger.warning(
            "Coach narration failed for user_id=%s batch_id=%s",
            user.id,
            batch_id,
            exc_info=True,
        )
        return None
