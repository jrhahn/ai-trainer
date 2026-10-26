"""What the activity analysis does with the model's answer, at every door it has.

The analysis of a batch of rides runs from the dashboard and onboarding
(``POST /ai/analyse-activities``) and, since ai-trainer-ops#306, once after an
intervals.icu connect has imported the last weeks (``services.history_review``).
Both read the model's answer the same way: its text fields as strings, the
assessment checked before anything is stored, the assessment and the login
summary recorded, and the plan changes committed through the shared pipeline
and told in the chat.
"""

from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services import assessment_pipeline, coach_summary, plan_pipeline

# The model may answer these with a dict or a list; the columns and the schema
# hold strings.
_TEXT_FIELDS = ("rideInsights", "lastRideFeedback", "loginSummary", "notes")


class UnparsableAnalysis(ValueError):
    """The model's assessment does not validate: nothing is stored from it."""


def checked_assessment(result: dict) -> schemas.RiderAssessmentSchema:
    """The model's assessment, validated, with its text fields made strings.

    Checked before any persistence or further model calls, so a malformed answer
    spends nothing more and stores nothing (ops#132).
    """
    for field in _TEXT_FIELDS:
        value = result.get(field)
        if value is not None and not isinstance(value, str):
            result[field] = json.dumps(value)
    try:
        return schemas.RiderAssessmentSchema.model_validate(result)
    except ValidationError as exc:
        raise UnparsableAnalysis("The analysis could not be parsed.") from exc


async def record_assessment(db: AsyncSession, user: models.User, result: dict) -> None:
    """The new assessment. Its inputs clear what was built from the old ones,
    the status badge included (ai-trainer-ops#245)."""
    await assessment_pipeline.record(
        db,
        user,
        estimated_ftp=result.get("estimatedFTP"),
        rider_type=result.get("riderType"),
        notes=result.get("notes"),
        hr_zones=result.get("hrZones"),
        ride_insights=result.get("rideInsights"),
        last_ride_feedback=result.get("lastRideFeedback"),
    )


async def record_login_summary(
    db: AsyncSession, user: models.User, result: dict
) -> None:
    """The login summary the model wrote from these inputs and rides.

    Stored once the rides' notifications have cleared the old one, and without
    a notification of its own (ai-trainer-ops#245).
    """
    if result.get("loginSummary"):
        await assessment_pipeline.record(
            db,
            user,
            notify=False,
            estimated_ftp=result.get("estimatedFTP"),
            login_summary=result.get("loginSummary"),
        )


async def commit_plan_updates(
    db: AsyncSession, user: models.User, raw_updates: list[dict], *, source: str
) -> plan_pipeline.PlanCommitResult | None:
    """The model's plan changes, through the shared constraint- and
    pin-respecting pipeline, then told in the chat.

    Persisted here rather than returned for the client to PUT its whole plan
    back, which reverted concurrent edits and rewrote pinned and completed days
    (#399). ``None`` when the model proposed nothing.
    """
    if not raw_updates:
        return None
    plan_row = await crud.get_training_plan(db, user.id)
    base_plan = plan_row.plan if plan_row is not None else []
    commit = await plan_pipeline.commit_plan_updates(
        db, user, raw_updates, base_plan=base_plan, source=source
    )
    await coach_summary.narrate_plan_changes(
        db,
        user,
        batch_id=commit.batch_id,
        source=source,
        applied_changes=commit.applied_changes,
    )
    return commit
