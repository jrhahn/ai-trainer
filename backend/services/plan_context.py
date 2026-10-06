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
from services import run_durability, training_monotony
from services.dates import app_today
from services.interference import find_interference
from services.plan_coherence import find_repeated_sessions
from services.plan_commitments import commitment_to_dict
from services.prompts import (
    plan_change_history_section,
    plan_coherence_section,
    plan_commitments_section,
    plan_interference_section,
    run_durability_section,
    training_monotony_section,
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
    # One read of the activity history for both audits that need it. They want the
    # same rows over the same window, and the alternative is two identical queries
    # on every prompt build.
    activity_rows = await athlete_activity_history(db, user_id)
    exposure = run_durability.run_exposure(activity_rows, today)
    distribution = training_monotony.load_distribution(activity_rows, today)
    return PlanWriterContext(
        change_history=plan_change_history_section(history_rows, today),
        coherence=_coherence_block(plan, today, exposure, distribution),
        commitments=plan_commitments_section(
            [commitment_to_dict(row) for row in commitment_rows], today
        ),
    )


async def athlete_activity_history(
    db,
    user_id: str,
    *,
    limit: int | None = None,
) -> list:
    """The athlete's recent activity rows, for every audit that reads history.

    Goes through ``crud.get_ride_metrics_history`` rather than a query of its own
    because that reader collapses the duplicate rows a re-import leaves behind.
    Both consumers care, and both are harmed in the direction that *hides* their
    finding: a duplicated run inflates the chronic figure and raises the run
    ceiling, and a duplicated day inflates one day's load, raising the standard
    deviation and lowering monotony.

    The default reads both modules' limits at call time rather than binding their
    maximum into the signature, for the reason ``training_monotony`` spells out:
    a default argument is evaluated once at import, so either module raising its
    own limit later would be silently ignored here.
    """
    if limit is None:
        limit = max(
            run_durability.HISTORY_ROW_LIMIT, training_monotony.HISTORY_ROW_LIMIT
        )
    return list(await crud.get_ride_metrics_history(db, user_id, limit=limit))


async def athlete_run_exposure(
    db,
    user_id: str,
    today,
    *,
    limit: int = run_durability.HISTORY_ROW_LIMIT,
) -> run_durability.RunExposure:
    """One athlete's running exposure, read from their recorded activities (#717).

    The query lives here rather than in ``services/run_durability`` so that module
    stays pure, and it goes through ``crud.get_ride_metrics_history`` rather than
    a query of its own because that reader collapses the duplicate rows a
    re-import leaves behind. A duplicated run would be counted twice, and the
    direction that errs in is the dangerous one: an inflated chronic figure raises
    the ceiling.

    ``today`` is required, with no default. An absent reference date makes
    ``run_exposure`` return an empty exposure, which is *not* the safe direction
    it looks like: an empty exposure hands a 200 km-a-month runner the beginner
    ceiling, and the gate then reverts perfectly good writes. A caller that forgot
    it should fail loudly rather than quietly re-classify the athlete as a
    beginner.

    Shared with the plan gate (``plan_pipeline._revert_new_run_overload``) so the
    figure the planner is given and the figure the gate enforces cannot drift —
    the same reason #666 moved the context construction into this file. Since #747
    the prompt build reads the rows once for both audits and calls
    ``run_exposure`` directly, so what this guarantees is narrower and still the
    part that matters: both paths derive the exposure from the same pure function
    over the same deduplicated reader.
    """
    return run_durability.run_exposure(
        await athlete_activity_history(db, user_id, limit=limit), today
    )


def _coherence_block(
    plan: list[dict] | None,
    today,
    exposure: run_durability.RunExposure,
    distribution: training_monotony.LoadDistribution | None,
) -> str:
    """The deterministic audits of the week, as one prompt block.

    Sections sharing one slot rather than another prompt parameter through four
    call sites: they answer the same question — what is wrong with this week that
    is decidable without an LLM — and go to the same place in every prompt that
    has them. Adding the interference audit (#715) and the durability ceiling
    (#717) to the block is therefore a change to this file, which is the whole
    reason #666 moved the construction here.

    The durability section is the one that speaks when nothing is wrong, because
    the ceiling is a constraint to plan inside rather than a defect to report;
    see ``prompts.run_durability_section``. It is skipped entirely for an athlete
    with no running history *and* no running in the plan — running is not in play
    for them, and a pure cyclist should not carry a paragraph about tibias in
    every prompt.

    The monotony section (#747) is the one that describes what *was done* rather
    than what is planned. It belongs in this block anyway: it is decidable without
    an LLM, it is about this week, and the writer has to know it before it writes —
    a flat week is fixed by placing an easy day, which is a planning act.
    """
    sections = [
        plan_coherence_section(find_repeated_sessions(plan, today), today),
        plan_interference_section(find_interference(plan, today), today),
        _durability_section(plan, today, exposure),
        training_monotony_section(distribution),
    ]
    return "\n\n".join(section for section in sections if section)


def _durability_section(
    plan: list[dict] | None, today, exposure: run_durability.RunExposure
) -> str:
    if not exposure.has_history and not run_durability.plan_has_running(plan, today):
        return ""
    ceiling = run_durability.run_volume_ceiling(exposure)
    findings = run_durability.find_run_overload(plan, exposure, ceiling, today)
    return run_durability_section(exposure, ceiling, findings, today)
