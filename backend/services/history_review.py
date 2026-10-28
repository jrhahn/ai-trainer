"""The plan is rebuilt once from the rides an intervals.icu connect brought in.

Connecting intervals.icu in Settings checked the key and nothing more: the
rides came in on the next dashboard visit, unseen, and the plan was only read
again (ai-trainer-ops#306). The connect imports the last weeks
(``routers.intervals``), and the coach looked at them once and adapted the
coming days. The owner's answer on #136, 2026-10-26, (c): the connect rebuilds
the whole plan at once from the new history, through the generation the night
uses (#206), and the coach says so in the chat. The generation records what the
plan was built from, so the night does not rebuild it again for the same
history: one change, told once, not a second one in the morning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import coach_summary, plan_generation, summary_pipeline

# The plan-change trigger, narrated in the chat (services.coach_summary).
SOURCE = "history_rebuild"

# Why the plan was rebuilt, as the dashboard names it (``plan_inputs.REASONS``).
REBUILT_FOR = "history"

# Said when the rebuilt plan came out as it was: the athlete waited for the
# import, and silence read as nothing having happened.
REBUILT_UNCHANGED = {
    "German": (
        "Ich habe deinen Plan aus deinen Fahrten der letzten Wochen neu aufgebaut. "
        "Er ist dabei gleich geblieben, also bleibt er, wie er ist."
    ),
    "English": (
        "I've rebuilt your plan from your rides of the last few weeks. It came out "
        "the same, so it stays as it is."
    ),
}

# Said when days changed but the narration of them failed: a rebuild is never
# silent (jrhahn/ai-trainer#664).
REBUILT = {
    "German": (
        "Ich habe deinen Plan aus deinen Fahrten der letzten Wochen neu aufgebaut. "
        "Was sich geändert hat, steht bei den Planänderungen."
    ),
    "English": (
        "I've rebuilt your plan from your rides of the last few weeks. What changed "
        "is listed under the plan changes."
    ),
}


@dataclass(frozen=True)
class HistoryReview:
    """Whether the plan was rebuilt from the rides, and how many days changed."""

    analysed: bool
    plan_changes: int = 0


NOT_REVIEWED = HistoryReview(analysed=False)


async def _advance_cursor(db: AsyncSession, user: models.User, newest: dict) -> None:
    """Put the sync's cursor on *newest*, never back in time.

    The ids are hashes, so a larger one is not a later ride: the cursor's own
    ride is looked up and kept when it started after *newest*, as on a second
    connect (review of #1177).
    """
    current = user.last_intervals_activity_id
    if current is not None:
        stored = await crud.get_ride_metric_by_strava_id(db, user.id, current)
        started = stored and (stored.activity_start_datetime or stored.activity_date)
        if started and started > (newest.get("start_date_local") or ""):
            return
    user.last_intervals_activity_id = newest["id"]


async def rebuild_from_imported_rides(
    db: AsyncSession,
    user: models.User,
    rides: list[dict],
    *,
    timezone_name: str | None,
    language: str | None,
) -> HistoryReview:
    """Rebuild the plan once from the history the connect brought in, and say so.

    *rides* are the imported rides, newest first. The import has stored them
    with their streams, and the generation reads them from there. Without a
    plan nothing happens: onboarding builds the first plan itself, and its
    opening message comes first.

    Raises what the generation raises (today's rebuilds used up, an answer that
    is no plan, a rate limit); the caller rolls back, and the dashboard's
    analysis runs instead, as it did when the look failed.
    """
    plan_row = await crud.get_training_plan(db, user.id)
    if plan_row is None or not plan_row.plan or not rides:
        return NOT_REVIEWED
    # Their chat words first, the interface next (ai-trainer-ops#121, #258).
    reply_language = (
        await summary_pipeline.athlete_language_of(db, user.id)
        or language
        or user.interface_language
    )
    commit = await plan_generation.rebuild_plan(db, user, timezone_name, source=SOURCE)
    await plan_generation.mark_rebuilt(
        db, user, [REBUILT_FOR], datetime.now(timezone.utc).isoformat()
    )
    # The dashboard's own analysis has nothing left to do, and the sync starts
    # after the newest ride rather than at it.
    user.intervals_analysis_complete = True
    await _advance_cursor(db, user, rides[0])
    told = await coach_summary.narrate_plan_changes(
        db,
        user,
        batch_id=commit.batch_id,
        source=SOURCE,
        applied_changes=commit.applied_changes,
        plan=commit.plan,
        reply_language=reply_language,
    )
    if told is None:
        said = REBUILT if commit.applied_changes else REBUILT_UNCHANGED
        await crud.create_chat_message(
            db,
            user.id,
            role="assistant",
            content=said.get(reply_language or "English", said["English"]),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    return HistoryReview(analysed=True, plan_changes=len(commit.applied_changes))
