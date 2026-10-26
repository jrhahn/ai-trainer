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

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from datetime import datetime, timedelta, timezone
from pathlib import Path

import crud
import schemas
from services import (
    goal_progress,
    plan_priors,
    run_durability,
    season_plan,
    training_monotony,
    training_phase,
)
from services.dates import app_today, app_today_iso
from services.interference import find_interference
from services.plan_coherence import find_repeated_sessions
from services.plan_commitments import commitment_to_dict
from services.prompts import (
    athlete_words_section,
    plan_change_history_section,
    plan_coherence_section,
    plan_commitments_section,
    plan_interference_section,
    run_durability_section,
    training_monotony_section,
)

async def race_events_for_prompt(db, user_id: str) -> list[dict]:
    """The athlete's race calendar as the prompts read it."""
    events = await crud.get_race_events(db, user_id)
    return [
        schemas.RaceEventResponse.model_validate(event, from_attributes=True).model_dump(by_alias=True)
        for event in events
    ]


async def active_availability_constraints(db, user_id: str, timezone_name: str | None) -> list[dict]:
    """The athlete's availability constraints that still hold, as the prompts read them.

    Expired one-off constraints are retired first.
    """
    today = app_today_iso(timezone_name=timezone_name)
    await crud.deactivate_expired_availability_constraints(db, user_id, today=today)
    constraints = await crud.list_active_availability_constraints(db, user_id, today=today)
    return [
        schemas.AthleteAvailabilityConstraintSchema.model_validate(
            constraint, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for constraint in constraints
    ]


def profile_with_availability_constraints(profile: dict, constraints: list[dict]) -> dict:
    """``profile`` with the constraints the writer must keep to, when there are any."""
    if not constraints:
        return profile
    return {**profile, "availabilityConstraints": constraints}


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
    # The coherence block's sections one by one, keyed for ``coach_saw``.
    sections: tuple[tuple[str, str], ...] = ()
    # Where a candidate plan departs from the planning priors, judged with this
    # athlete's inputs, so a writer can be held to the hard ones after it has
    # written (ai-trainer-ops#87). ``None`` when the user is gone.
    prior_check: Callable[[list[dict] | None], list[plan_priors.PriorDeparture]] | None = None


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
    phase = await training_phase_section_for_user(db, user_id, today, activity_rows)
    season = await season_section_for_user(db, user_id, plan, today, activity_rows)
    goals = await goal_section_for_user(db, user_id, today)
    inputs = await prior_inputs(db, user_id, today, activity_rows)
    priors = _priors_section(plan, today, inputs) if inputs else ""
    sections = _coherence_sections(
        plan,
        today,
        exposure,
        distribution,
        phase=phase,
        season=season,
        goals=goals,
        priors=priors,
    )
    return PlanWriterContext(
        change_history=plan_change_history_section(history_rows, today),
        coherence="\n\n".join(text for _key, text in sections),
        sections=tuple(sections),
        commitments=plan_commitments_section(
            [commitment_to_dict(row) for row in commitment_rows], today
        ),
        prior_check=(
            partial(plan_priors.find_prior_departures, today=today, **inputs)
            if inputs
            else None
        ),
    )


async def training_phase_section_for_user(
    db, user_id: str, today, activity_rows: list | None = None
) -> str:
    """Where the athlete is in their season, as a prompt block (ai-trainer-ops#64).

    Here rather than in ``services/training_phase`` so that module stays pure, and
    shared by every writer: the chat, the nightly run and auto-adapt get it through
    ``plan_writer_context``; a full regeneration, which builds no writer context,
    calls this directly. Two writers planning different phases for the same week
    would undo each other every night.
    """
    if activity_rows is None:
        activity_rows = await athlete_activity_history(db, user_id)
    user = await crud.get_user_by_id(db, user_id)
    phase = training_phase.training_phase(
        today,
        await _race_dates(db, user_id, user),
        activity_rows,
        latitude=await _home_latitude(db, user_id),
    )
    return training_phase.training_phase_section(phase)


async def _race_dates(db, user_id: str, user) -> list:
    """The race calendar and the profile's race date: every race the plan knows."""
    race_dates = [event.date for event in await crud.get_race_events(db, user_id)]
    if user is not None and user.race_date:
        race_dates.append(user.race_date)
    return race_dates


async def _home_latitude(db, user_id: str) -> float | None:
    home = await crud.get_athlete_home_location(db, user_id)
    return home.latitude if home is not None else None


async def season_weeks_for_user(
    db, user_id: str, today, activity_rows: list | None = None
) -> list[season_plan.SeasonWeek]:
    """The weeks ahead with their targets (ai-trainer-ops#98), from the same
    inputs as the phase, so the two cannot disagree about the current week."""
    if activity_rows is None:
        activity_rows = await athlete_activity_history(db, user_id)
    user = await crud.get_user_by_id(db, user_id)
    if user is None:
        return []
    return season_plan.season_weeks(
        today,
        await _race_dates(db, user_id, user),
        activity_rows,
        weekly_hours=user.weekly_hours,
        latitude=await _home_latitude(db, user_id),
    )


async def season_section_for_user(
    db, user_id: str, plan: list[dict] | None, today, activity_rows: list | None = None
) -> str:
    """The season plan as a prompt block, with its goals and the plan's departures.

    Shared by every writer for the reason the phase is.
    """
    weeks = await season_weeks_for_user(db, user_id, today, activity_rows)
    stored = await crud.get_week_goals(
        db, user_id, [week.start.isoformat() for week in weeks]
    )
    return season_plan.season_section(
        weeks,
        season_plan.current_goals(weeks, stored),
        season_plan.find_week_departures(plan, weeks, today),
        today,
    )


async def priors_section_for_user(
    db, user_id: str, plan: list[dict] | None, today, activity_rows: list
) -> str:
    """Where the plan departs from the planning priors (ai-trainer-ops#26).

    Reported to the writer here. The hard ones are also enforced after a write,
    through ``PlanWriterContext.prior_check`` (ai-trainer-ops#87).
    """
    inputs = await prior_inputs(db, user_id, today, activity_rows)
    return _priors_section(plan, today, inputs) if inputs else ""


async def prior_check_for_user(
    db, user_id: str, today, activity_rows: list | None = None
) -> Callable[[list[dict] | None], list[plan_priors.PriorDeparture]] | None:
    """The departures of a candidate plan, judged with ``user_id``'s inputs.

    For the writers that build no ``PlanWriterContext``: a full regeneration,
    the adaptation after a ride, and the persist gate's notes (ai-trainer-ops#87).
    """
    if activity_rows is None:
        activity_rows = await athlete_activity_history(db, user_id)
    inputs = await prior_inputs(db, user_id, today, activity_rows)
    if inputs is None:
        return None
    return partial(plan_priors.find_prior_departures, today=today, **inputs)


async def prior_inputs(db, user_id: str, today, activity_rows: list) -> dict | None:
    """Everything ``find_prior_departures`` reads besides the plan, or ``None``."""
    user = await crud.get_user_by_id(db, user_id)
    if user is None:
        return None
    return {
        "weekly_hours": user.weekly_hours,
        "training_days": user.training_days,
        "race_dates": await _race_dates(db, user_id, user),
        "recent_weekly_hours": training_phase.load_rhythm(activity_rows, today).weekly_hours,
        # What the athlete said about being ill or tired (#18): the writer plans
        # from it even when the plan does not depart from anything yet.
        "states": await crud.list_relevant_states(db, user_id, today),
        "longest_ride": plan_priors.longest_ride_minutes(activity_rows, today),
        # What is already done this calendar week, for the stated hours (#116).
        "done_minutes_by_date": plan_priors.recorded_minutes_by_date(activity_rows, today),
    }


def _priors_section(plan: list[dict] | None, today, inputs: dict) -> str:
    departures = plan_priors.plan_priors_section(
        plan_priors.find_prior_departures(plan, today, **inputs)
    )
    return "\n\n".join(
        part
        for part in (plan_priors.athlete_states_section(inputs["states"], today), departures)
        if part
    )


# How far back a run nobody asked for reads the athlete's chat (#152). Long
# enough for "next weekend I'm in the mountains" said on a Thursday to reach the
# Saturday run; short enough not to replan from last week's moods.
ATHLETE_WORDS_DAYS = 3


async def athlete_words_for_user(
    db, user_id: str, *, now: datetime | None = None, timezone_name: str | None = None
) -> str:
    """What the athlete wrote in the chat in the last :data:`ATHLETE_WORDS_DAYS`
    days, as the prompt section the automated runs read (ai-trainer-ops#152)."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=ATHLETE_WORDS_DAYS)
    messages = await crud.get_athlete_messages_since(db, user_id, since)
    return athlete_words_section(messages, timezone_name)


# Goal progress looks back twelve weeks, longer than the audits sharing
# ``athlete_activity_history``, so it reads its own, larger window.
GOAL_HISTORY_ROW_LIMIT = 500


async def goal_section_for_user(db, user_id: str, today) -> str:
    """The athlete's measurable goals and how far away each is (ai-trainer-ops#65).

    Shared by every writer for the reason the phase is: the chat, the nightly run
    and auto-adapt get it through ``plan_writer_context``, and a full regeneration
    calls this directly.
    """
    goals = await crud.list_active_goals(db, user_id)
    if not goals:
        return ""
    activity_rows = await athlete_activity_history(
        db, user_id, limit=GOAL_HISTORY_ROW_LIMIT
    )
    return goal_progress.goal_progress_section(
        [
            (
                goal,
                goal_progress.goal_progress(
                    goal.metric, goal.scope, goal.target, activity_rows, today
                ),
            )
            for goal in goals
        ]
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
    phase: str = "",
    goals: str = "",
    priors: str = "",
    season: str = "",
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

    The training phase (ai-trainer-ops#64) comes first, and the season plan
    (#98) after it: they are the frame the other findings are fixed inside, like
    the durability ceiling a constraint and not a defect.
    """
    sections = _coherence_sections(
        plan,
        today,
        exposure,
        distribution,
        phase=phase,
        season=season,
        goals=goals,
        priors=priors,
    )
    return "\n\n".join(text for _key, text in sections)


def _coherence_sections(
    plan, today, exposure, distribution, *, phase="", season="", goals="", priors=""
) -> list[tuple[str, str]]:
    """The non-empty sections of :func:`_coherence_block`, in order, with keys."""
    sections = [
        ("phase", phase),
        ("season", season),
        ("repeats", plan_coherence_section(find_repeated_sessions(plan, today), today)),
        (
            "interference",
            plan_interference_section(find_interference(plan, today), today),
        ),
        ("running", _durability_section(plan, today, exposure)),
        ("monotony", training_monotony_section(distribution)),
        ("priors", priors),
        ("goals", goals),
    ]
    return [(key, text) for key, text in sections if text]


# What the coach was given, for the athlete to read under a reply
# (ai-trainer-ops#17, #81 Q24): each section's title and the function that
# computes it, so the link goes to the rule rather than to the prose around it.
SECTION_SOURCES = {
    "phase": ("Training phase", training_phase.training_phase),
    "season": ("Season plan", season_plan.season_weeks),
    "repeats": ("Repeated sessions", find_repeated_sessions),
    "interference": ("Strength before key sessions", find_interference),
    "running": ("Running ceiling", run_durability.run_volume_ceiling),
    "monotony": ("Load distribution", training_monotony.load_distribution),
    "priors": ("Planning priors", plan_priors.find_prior_departures),
    "goals": ("Goals", goal_progress.goal_progress),
}
_BACKEND_DIR = Path(__file__).resolve().parent.parent


def coach_saw(sections, ref: str) -> list[dict]:
    """``sections`` as the athlete reads them, each linked to its code at ``ref``."""
    items = []
    for key, text in sections:
        title, function = SECTION_SOURCES[key]
        path = Path(inspect.getsourcefile(function)).resolve()
        items.append(
            {
                "key": key,
                "title": title,
                "text": text,
                "source": {
                    "path": f"backend/{path.relative_to(_BACKEND_DIR).as_posix()}",
                    "line": inspect.getsourcelines(function)[1],
                    "symbol": function.__name__,
                    "ref": ref,
                },
            }
        )
    return items


def _durability_section(
    plan: list[dict] | None, today, exposure: run_durability.RunExposure
) -> str:
    if not exposure.has_history and not run_durability.plan_has_running(plan, today):
        return ""
    ceiling = run_durability.run_volume_ceiling(exposure)
    findings = run_durability.find_run_overload(plan, exposure, ceiling, today)
    return run_durability_section(exposure, ceiling, findings, today)
