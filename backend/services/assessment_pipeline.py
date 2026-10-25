"""Assessment pipeline node: rider-assessment inputs feed the login summary.

The rider assessment (estimated FTP, ride insights, last-ride feedback, notes)
is one of the two inputs to the dashboard "recent training summary" — the
training plan is the other (see :mod:`services.summary_pipeline`). When those
inputs change through a write that does *not* itself regenerate the summary,
this node fans the change out to downstream pipelines so the stored summary is
invalidated and lazily regenerated on the next dashboard load.

This is a source node (no upstream). :func:`record` is the one writer of the
assessment, and it notifies unless told not to: five places wrote the
assessment, two of them without a notification, so the badge kept describing
the week before an analysis (ai-trainer-ops#245).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services.pipeline_graph import graph

PIPELINE_NAME = "assessment"

graph.register(PIPELINE_NAME)


async def notify_changed(db: AsyncSession, user: models.User) -> None:
    """Signal that rider-assessment inputs changed (cascades to the summary)."""
    await graph.notify_changed(PIPELINE_NAME, db=db, user=user)


async def record(
    db: AsyncSession, user: models.User, *, notify: bool = True, **fields: Any
) -> models.RiderAssessment:
    """Write the rider assessment and signal the change.

    ``fields`` are those of :func:`crud.upsert_rider_assessment`. ``notify=False``
    is for writing the login summary itself, the output of the summary node:
    notifying would clear the summary that was just written.
    """
    assessment = await crud.upsert_rider_assessment(db, user.id, **fields)
    if notify:
        await notify_changed(db, user)
    return assessment
