"""Assessment pipeline node: rider-assessment inputs feed the login summary.

The rider assessment (estimated FTP, ride insights, last-ride feedback, notes)
is one of the two inputs to the dashboard "recent training summary" — the
training plan is the other (see :mod:`services.summary_pipeline`). When those
inputs change through a write that does *not* itself regenerate the summary,
this node fans the change out to downstream pipelines so the stored summary is
invalidated and lazily regenerated on the next dashboard load.

This is a source node (no upstream): callers invoke :func:`notify_changed`
right after persisting an assessment-input change.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

import models
from services.pipeline_graph import graph

PIPELINE_NAME = "assessment"

graph.register(PIPELINE_NAME)


async def notify_changed(db: AsyncSession, user: models.User) -> None:
    """Signal that rider-assessment inputs changed (cascades to the summary)."""
    await graph.notify_changed(PIPELINE_NAME, db=db, user=user)
