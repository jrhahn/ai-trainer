"""The context a plan writer needs before it touches the week (#666).

Two blocks decide whether a plan write is a coaching decision or a guess:

- what changed recently and who changed it (#652), so a writer knows what the
  athlete and the coach agreed hours ago rather than inferring intent from the
  end state;
- which adjacent days already collide (#659), stated as a fact rather than left
  to be noticed.

Until #666 both were built inline in ``routers/ai.py``'s ask-trainer handler and
existed nowhere else, so the nightly job — 309 applied day changes in 30 days of
production against the coach's 45 — planned the week with neither. Building them
here means the chat and the automated triggers cannot drift apart: adding a
block benefits every writer, and forgetting to pass one is a change to this
file rather than an omission in one call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import crud
from services.dates import app_today
from services.plan_coherence import find_repeated_sessions
from services.plan_commitments import commitment_to_dict
from services.prompts import (
    plan_change_history_section,
    plan_coherence_section,
    plan_commitments_section,
)

# How far back the change log is read. Long enough to cover an overnight run and
# the conversation around it, short enough that the coach is not re-reading last
# month's churn on every message.
DEFAULT_HISTORY_LIMIT = 60


@dataclass(frozen=True, slots=True)
class PlanWriterContext:
    """Prompt sections shared by every trigger that may rewrite the plan."""

    change_history: str = ""
    coherence: str = ""
    commitments: str = ""


async def plan_writer_context(
    db,
    user_id: str,
    plan: list[dict] | None,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> PlanWriterContext:
    """Build both sections for ``user_id``'s current ``plan``.

    Each section is empty when there is nothing to say — an athlete with no
    change history and a coherent week costs nothing extra in the prompt.

    ``now`` matters: both sections are relative to a reference date — the change
    log is windowed around it and the coherence horizon runs forward from it — so
    a caller that has its own notion of "today" (the nightly job takes one) must
    pass it, or both sections silently come back empty.
    """
    today = app_today(now, timezone_name)
    history_rows = await crud.list_plan_day_history(db, user_id, limit=history_limit)
    commitment_rows = await crud.list_active_plan_commitments(
        db, user_id, today=today.isoformat()
    )
    return PlanWriterContext(
        change_history=plan_change_history_section(history_rows, today),
        coherence=plan_coherence_section(find_repeated_sessions(plan, today), today),
        commitments=plan_commitments_section(
            [commitment_to_dict(row) for row in commitment_rows], today
        ),
    )
