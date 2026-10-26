"""The coach looks once at the rides an intervals.icu connect brought in.

Connecting intervals.icu in Settings checked the key and nothing more: the
rides came in on the next dashboard visit, unseen, and the plan was only read
again (ai-trainer-ops#306). Now the connect imports the last weeks
(``routers.intervals``), and this runs once over them: the model's analysis, as
the dashboard's, then the coming days adapted through the shared pipeline and
told in the chat, or one sentence that nothing needed to change. Never a
rebuild of the plan (jrhahn/ai-trainer#664).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import activity_analysis, ai_service, metrics_service, summary_pipeline
from services.llm import resolve_user_provider
from services.token_accounting import track_llm_usage

# The plan-change trigger, narrated in the chat (services.coach_summary).
SOURCE = "history_review"

# The newest rides the model is shown: six weeks rarely hold more, and the
# analysis stays one batch.
MAX_RIDES = 30

# Said when the coach looked and the plan stays: the athlete waited for the
# import, and silence read as nothing having happened.
NOTHING_TO_CHANGE = {
    "German": (
        "Ich habe mir deine Fahrten der letzten Wochen angesehen. Dein Plan für die "
        "nächsten Tage passt dazu, also bleibt er, wie er ist."
    ),
    "English": (
        "I've looked at your rides from the last few weeks. Your plan for the "
        "coming days fits them, so it stays as it is."
    ),
}


@dataclass(frozen=True)
class HistoryReview:
    """Whether the coach looked at the rides, and how many days it changed."""

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


async def review_imported_rides(
    db: AsyncSession,
    user: models.User,
    rides: list[dict],
    *,
    timezone_name: str | None,
    language: str | None,
) -> HistoryReview:
    """Analyse the imported *rides* (newest first) once and adapt the plan.

    *rides* are the analysis's own payloads, as the dashboard sends them. They
    are stored already, by the import, with their streams; nothing here stores
    them again. Without a plan nothing happens: onboarding analyses and builds
    the first plan itself, and its opening message comes first.

    Raises what the model call raises (a spent budget, a rate limit, an
    unparsable answer); the caller rolls back, and the dashboard's analysis
    runs instead, as before.
    """
    plan_row = await crud.get_training_plan(db, user.id)
    plan = plan_row.plan if plan_row is not None else []
    if not plan or not rides:
        return NOT_REVIEWED
    shown = rides[:MAX_RIDES]
    # Their chat words first, the interface next (ai-trainer-ops#121, #258).
    reply_language = (
        await summary_pipeline.athlete_language_of(db, user.id)
        or language
        or user.interface_language
    )
    async with track_llm_usage(db, user, source="job:intervals-history-review"):
        result = await ai_service.analyse_strava_activities(
            shown,
            provider=resolve_user_provider(user),
            streams_by_id={},
            max_heart_rate=user.max_heart_rate,
            training_plan=plan,
            user_ftp=int(user.current_ftp) if user.current_ftp else None,
            timezone_name=timezone_name,
            threshold_pace_seconds_per_km=(
                await metrics_service.get_effective_threshold_pace(db, user)
            ),
            reply_language=reply_language,
        )
        activity_analysis.checked_assessment(result)
        await activity_analysis.record_assessment(db, user, result)
        # The dashboard's own analysis has nothing left to do, and the sync
        # starts after the newest ride rather than at it.
        user.intervals_analysis_complete = True
        await _advance_cursor(db, user, shown[0])
        await activity_analysis.record_login_summary(db, user, result)
        commit = await activity_analysis.commit_plan_updates(
            db, user, result.get("planUpdates") or [], source=SOURCE
        )
    changes = len(commit.applied_changes) if commit is not None else 0
    if not changes:
        await crud.create_chat_message(
            db,
            user.id,
            role="assistant",
            content=NOTHING_TO_CHANGE.get(
                reply_language or "English", NOTHING_TO_CHANGE["English"]
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    return HistoryReview(analysed=True, plan_changes=changes)
