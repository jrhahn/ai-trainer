"""Rides pipeline node: the athlete's stored activities changed.

A ride that arrives through any door changes what the dashboard says. The
recent-training summary describes the newest activity, and the status badge
counts the week's sessions from the stored rides. Only the plan and the
assessment were ever notified, so an import that touched neither left both
describing the week before the ride. Three of the doors cleared the summary by
hand, and none of them the badge (ai-trainer-ops#245).

This is a source node: whatever writes ride rows calls :func:`notify_changed`
once they are stored and matched. Those are the background sync
(services/activity_sync.py), the intervals.icu and Strava history imports, a
.fit upload, the activity analysis, a session the athlete logged by hand
(services/logged_sessions.py), and the athlete's own answer about a ride's
feel or purpose.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

import models
from services.pipeline_graph import graph

PIPELINE_NAME = "rides"

graph.register(PIPELINE_NAME)


async def notify_changed(db: AsyncSession, user: models.User) -> None:
    """Signal that the user's stored rides changed (cascades to the summary and the badge)."""
    await graph.notify_changed(PIPELINE_NAME, db=db, user=user)
