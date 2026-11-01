"""Backend-owned daily maintenance for stale training plans."""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services import ai_service
from services.ai_service import AIRateLimitError
from services import coach_summary
from services import freshness_allocation
from services import logged_sessions
from services.dates import app_today_iso
from services.llm import resolve_user_provider
from services import plan_context
from services import plan_generation
from services import plan_inputs
from services import plan_pipeline
from services import plan_translations
from services import ride_matching
from services import season_plan
from services import week_goals
from services.prompts import PLAN_HORIZON_DAYS, ride_metrics_context_section
from services.weather_service import training_weather_context_for_user
from services.token_accounting import track_llm_usage
from services.per_user_jobs import PerUserJob

logger = logging.getLogger(__name__)

DAILY_PLAN_MAINTENANCE_HOUR = 2


def has_stale_incomplete_days(plan: list[dict], today: str) -> bool:
    return any(day.get("date", "") < today and not day.get("completed") for day in plan)


# A session missed last week has already been dealt with, or has not and will
# not be. Re-triggering a full plan review over it every night for the rest of
# the plan's life is what turned this job into 30 batches in 30 days.
MISSED_SESSION_LOOKBACK_DAYS = 3

# Below this many days ahead the rolling window needs refilling. Sits under
# PLAN_HORIZON_DAYS so a plan that is merely a day or two short is left alone
# rather than topped up nightly.
MIN_FUTURE_PLAN_DAYS = 10


def recently_missed_sessions(
    plan: list[dict],
    today: str,
    *,
    lookback_days: int = MISSED_SESSION_LOOKBACK_DAYS,
    trained_dates: Collection[str] = (),
) -> list[dict]:
    """Sessions in the last ``lookback_days`` that were scheduled and not done.

    Activity import marks a day completed when a matching ride lands (#473), so
    an uncompleted past day means the athlete did not train — something a coach
    would react to. Bounded, because :func:`has_stale_incomplete_days` was true
    for the entire remaining life of the plan once any single day was missed.

    ``trained_dates`` are dates with a recorded activity. A session on such a
    date is not called missed even when no flag says it was done: a ride that
    matched nothing, or a day whose flag a plan save cleared before
    ai-trainer-ops#111, was still a day the athlete trained. Copying its
    session forward is the worse mistake.
    """
    try:
        cutoff = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    except (TypeError, ValueError):
        return []
    trained = set(trained_dates)
    return [
        day
        for day in plan
        if isinstance(day, dict)
        and cutoff <= str(day.get("date") or "") < today
        and not day.get("completed")
        and str(day.get("date") or "") not in trained
        and str(day.get("workoutType") or "").strip().lower() not in ("rest", "off", "")
    ]


def future_day_count(plan: list[dict], today: str) -> int:
    """How many days the athlete still has ahead of them, today included."""
    return sum(
        1
        for day in plan
        if isinstance(day, dict) and str(day.get("date") or "") >= today
    )


def maintenance_reasons(
    plan: list[dict],
    today: str,
    *,
    lookback_days: int = MISSED_SESSION_LOOKBACK_DAYS,
    min_future_days: int = MIN_FUTURE_PLAN_DAYS,
    trained_dates: Collection[str] = (),
) -> list[str]:
    """Why tonight's run is worth making. Empty means: leave the plan alone.

    Deliberately two reasons, both decidable from the plan itself. A new
    availability constraint is *not* one: constraints are enforced at the
    pipeline gate on every write (``sanitize_plan_for_constraints``), so they
    take effect without an LLM run. Weather is not one either — recognising a
    *relevant* change needs a stored forecast baseline that does not exist yet;
    until it does, claiming to react to weather nightly would just be the old
    unconditional rewrite wearing a reason.
    """
    reasons: list[str] = []
    if recently_missed_sessions(
        plan, today, lookback_days=lookback_days, trained_dates=trained_dates
    ):
        reasons.append("missed_session")
    if future_day_count(plan, today) < min_future_days:
        reasons.append("short_horizon")
    return reasons


async def _week_targets_moved(
    db: AsyncSession, user: models.User, plan: list[dict], today: date
) -> bool:
    """Whether a week the plan covers changed under its days (ai-trainer-ops#98).

    A new race, say, turns next week into a taper. The goals of the weeks
    ahead are written here too, for an athlete who has not opened the
    dashboard since. A week that moved counts once: the run it starts may keep
    the days as they are, and asking again every night is the churn of #666.
    """
    weeks = await plan_context.season_weeks_for_user(db, user.id, today)
    try:
        await week_goals.ensure_week_goals(
            db, user, weeks, today, source="step:week-goals"
        )
    except Exception:
        # The goal is a sentence on the dashboard; the plan does not wait for it.
        logger.warning("Week goals failed user_id=%s", user.id, exc_info=True)
    last_planned = today + timedelta(days=PLAN_HORIZON_DAYS - 1)
    covered = [week for week in weeks if week.start <= last_planned]
    # A week with no goal row yet has no fitted mark to move from. Its days were
    # written against it as it is, and it is picked up once a goal is written.
    moved = await week_goals.moved_weeks(db, user.id, covered)
    if not moved:
        return False
    week_goals.mark_fitted(moved)
    departing = {
        d.week.isoformat()
        for d in season_plan.find_week_departures(plan, covered, today)
    }
    return any(row.week_start in departing for row, _ in moved)


def apply_maintenance_updates(
    plan: list[dict],
    plan_updates: list[dict] | None,
    today: str,
    *,
    horizon_days: int = PLAN_HORIZON_DAYS,
) -> list[dict] | None:
    """Fold this run's updates into the stored plan. ``None`` when it changed nothing.

    Two kinds of update, and the distinction matters. A day already in the plan
    is *patched* through the canonical merge, so a day the run did not name comes
    out byte-identical — that is the whole point of #666, and the reason the
    nightly job stopped handing back eleven freshly-worded days every night.

    A date not yet in the plan is an *addition*, which extends the rolling window.
    ``apply_plan_updates`` cannot do that (it iterates the existing plan), and
    until now only the sync-driven full regeneration lengthened the plan at all —
    the trigger removed in #664. Additions are accepted only inside
    ``[today, today + horizon_days]``: a run that wants to append last Tuesday, or
    a day three months out, has lost track of the calendar and is not extending
    anything.
    """
    if not plan_updates:
        return None
    try:
        today_date = date.fromisoformat(today)
    except (TypeError, ValueError):
        return None
    horizon = (today_date + timedelta(days=horizon_days)).isoformat()

    existing_dates = {
        str(day.get("date"))
        for day in plan
        if isinstance(day, dict) and day.get("date")
    }
    patches = [u for u in plan_updates if str(u.get("date")) in existing_dates]
    additions = _valid_additions(
        [
            u
            for u in plan_updates
            if str(u.get("date")) not in existing_dates
            and today <= str(u.get("date") or "") <= horizon
        ]
    )
    if not patches and not additions:
        return None

    patched = plan_pipeline.apply_plan_updates(plan, patches)
    proposed = list(patched if patched is not None else plan)
    proposed.extend(additions)
    return proposed


def _valid_additions(additions: list[dict]) -> list[dict]:
    """The additions that are real days, each with a slot of its own.

    Patches go through the canonical merge; additions went into the plan raw
    (ai-trainer-ops#113). "2026-13-45" compares as a string inside the window,
    fractional watts were kept as written, and two additions on one new date
    both took slot 0, one session identity for two sessions.
    """
    valid: list[dict] = []
    taken: dict[str, set[int]] = {}
    for addition in additions:
        try:
            date.fromisoformat(str(addition.get("date") or ""))
            day = schemas.PlanDay.model_validate(addition)
        except ValueError:  # pydantic's ValidationError is one too
            logger.warning(
                "Dropping an invalid nightly addition for %r", addition.get("date")
            )
            continue
        used = taken.setdefault(day.date, set())
        if day.slot in used:
            day.slot = max(used) + 1
        used.add(day.slot)
        valid.append(day.model_dump(by_alias=True, exclude_none=True))
    return valid


async def _mark_done_sessions(
    db: AsyncSession, user: models.User, plan: list[dict]
) -> list[dict]:
    """``plan`` with every session a log or a matched ride shows was done marked so.

    Both writers already do this when the log or the ride arrives. Running them
    here heals the plans written before ai-trainer-ops#111: a plan save used to
    clear ``completed`` on every day, and a hand-logged session was never marked
    at all. Idempotent, and a no-op on the common night.
    """

    async def reread() -> list[dict]:
        row = await crud.get_training_plan(db, user.id)
        return row.plan if row is not None else plan

    if await logged_sessions.complete_logged_sessions(db, user, plan=plan):
        plan = await reread()
    rides = await crud.get_ride_metrics_history(db, user.id, limit=30)
    if await ride_matching.mark_matched_days_completed(db, user, plan, rides):
        plan = await reread()
    return plan


async def _trained_dates(
    db: AsyncSession, user_id: str, plan: list[dict], today: str
) -> set[str]:
    """The plan's recent past dates on which the athlete has a recorded activity."""
    cutoff = (
        date.fromisoformat(today) - timedelta(days=MISSED_SESSION_LOOKBACK_DAYS)
    ).isoformat()
    recent = sorted(
        {
            str(day.get("date"))
            for day in plan
            if isinstance(day, dict) and cutoff <= str(day.get("date") or "") < today
        }
    )
    if not recent:
        return set()
    return await crud.get_recorded_activity_dates(db, user_id, recent)


async def rebuild_if_inputs_changed(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> bool:
    """Rebuild the whole plan once when what it was built from moved
    (ai-trainer-ops#206).

    The nightly half of the automatic rebuild: new rides and answered questions
    nobody made by hand are batched here into one rebuild a night, not one per
    ride. A change the athlete made and that has not been rebuilt yet (today's
    rebuilds were used up) goes along. Within the same daily cap as every
    rebuild. False when nothing moved, the cap is reached or generation failed.
    """
    reasons = await plan_inputs.changes_since_generation(db, user)
    if not reasons:
        return False
    # Counted on the same day as every rebuild, the athlete's own.
    if await plan_generation.rebuilds_left(db, user, timezone_name) <= 0:
        return False

    try:
        await plan_generation.generate_plan_for_user(db, user, timezone_name)
    except (
        plan_generation.RebuildLimitReached,
        plan_generation.PlanGenerationFailed,
        AIRateLimitError,
    ) as exc:
        logger.warning(
            "Nightly rebuild skipped user_id=%s reason=%s", user.id, type(exc).__name__
        )
        return False
    except Exception:  # noqa: BLE001 - a provider error must not cost the night's adaptation
        logger.warning("Nightly rebuild failed user_id=%s", user.id, exc_info=True)
        return False
    await plan_generation.mark_rebuilt(
        db, user, reasons, (now or datetime.now(timezone.utc)).isoformat()
    )
    logger.info("Nightly rebuild user_id=%s reasons=%s", user.id, ",".join(reasons))
    return True


async def adapt_plan(
    db: AsyncSession,
    user: models.User,
    plan: list[dict],
    *,
    feedback: list[dict],
    source: str,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> plan_pipeline.PlanCommitResult | None:
    """Have the model adapt the coming days, commit what it proposes and say so
    in the chat: the one adapt path (ai-trainer-ops#246).

    The nightly run and the adaptation after a session that signals fatigue,
    illness or pain both take it, with the same context: the rides' load, the
    weather, the races, the availability constraints, who the athlete is and
    what their freshness is for (#602), the plan's recent changes and
    collisions (#666), what the athlete and the coach agreed in the chat, and
    the athlete's own words (#152). Returns the commit, or ``None`` when the
    model changed nothing.
    """
    today = app_today_iso(now, timezone_name)
    rider_assessment = None
    if user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)

    recent_metrics = await crud.get_ride_metrics_history(db, user.id, limit=30)
    metrics_section = ride_metrics_context_section(recent_metrics, timezone_name)
    weather_section = await training_weather_context_for_user(db, user.id)
    constraints = await plan_context.active_availability_constraints(
        db, user.id, timezone_name, now=now
    )
    profile = plan_context.profile_with_availability_constraints(
        schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True),
        constraints,
    )
    # Who this athlete is and what their freshness is for (#602).
    athlete_model_section = await freshness_allocation.athlete_model_section_for_user(
        db, user, timezone_name=timezone_name
    )
    # The blocks the chat coach has had since #652/#659: without them a writer
    # plans the week knowing nothing of what the athlete and the coach agreed,
    # and cannot see a collision it is about to inherit or create (#666).
    writer_context = await plan_context.plan_writer_context(
        db, user.id, plan, now=now, timezone_name=timezone_name
    )

    plan_updates = await ai_service.adapt_training_plan(
        plan,
        feedback,
        profile,
        provider=resolve_user_provider(user),
        rider_assessment=rider_assessment,
        metrics_history_section=metrics_section,
        weather_context_section=weather_section,
        race_events=await plan_context.race_events_for_prompt(db, user.id),
        timezone_name=timezone_name,
        athlete_model_section=athlete_model_section,
        plan_change_history=writer_context.change_history,
        plan_coherence_warnings=writer_context.coherence,
        plan_commitments=writer_context.commitments,
        # Held to the hard priors once (ai-trainer-ops#87).
        # The plan the commit would store, the athlete's limits applied: a
        # week over the stated hours before a per-session limit shortens it is
        # no departure (ai-trainer-ops#336, review of 2026-11-01 C5).
        prior_check=plan_pipeline.update_prior_check(
            writer_context.prior_check, plan, constraints=constraints
        ),
        # What the athlete said in the chat, not only what was extracted from
        # it (ai-trainer-ops#152).
        athlete_words=await plan_context.athlete_words_for_user(
            db, user.id, now=now, timezone_name=timezone_name
        ),
    )
    proposed_plan = apply_maintenance_updates(plan, plan_updates, today)
    if proposed_plan is None:
        return None

    # Hard-constraint enforcement, concurrent-edit protection and persistence
    # are owned by the shared plan pipeline so every trigger behaves identically.
    commit = await plan_pipeline.commit_plan(
        db,
        user,
        proposed_plan,
        base_plan=plan,
        source=source,
        now=now,
        timezone_name=timezone_name,
    )
    # Explain the change to the athlete in one chat message. In a savepoint of
    # its own (#281): a failed narration never undoes the plan it explains.
    try:
        async with db.begin_nested():
            await coach_summary.narrate_plan_changes(
                db,
                user,
                batch_id=commit.batch_id,
                source=source,
                applied_changes=commit.applied_changes,
                plan=commit.plan,
                profile=profile,
                rider_assessment=rider_assessment,
                training_load_section=metrics_section,
                # The same forecast the adaptation acted on, so a session moved
                # off a 38 C day is explained as exactly that.
                weather_context_section=weather_section,
            )
    except Exception:
        logger.warning(
            "Plan-change narration failed user_id=%s source=%s",
            user.id,
            source,
            exc_info=True,
        )
    return commit


async def adapt_after_feedback(
    db: AsyncSession,
    user: models.User,
    plan: list[dict],
    feedback_entry: dict,
    *,
    timezone_name: str | None = None,
) -> None:
    """Adapt the plan after a session whose feedback signals fatigue, illness or
    pain, by the one adapt path (ai-trainer-ops#246). Best-effort: the rating
    that triggered it stands if this fails."""
    try:
        async with db.begin_nested():
            await adapt_plan(
                db,
                user,
                plan,
                feedback=[feedback_entry],
                source="auto_adapt",
                timezone_name=timezone_name,
            )
    except Exception:
        logger.warning("Auto-adaptation after flagged workout failed", exc_info=True)


async def maintain_user_training_plan(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> bool:
    plan_row = await crud.get_training_plan(db, user.id)
    plan = plan_row.plan if plan_row is not None else []
    if not plan or not user.is_onboarded:
        return False

    today = app_today_iso(now, timezone_name)
    # "Done" has to be true in the plan before anything reads it: the reasons
    # below, the adapt prompt, which asks the model to reschedule every past
    # session not marked completed (ai-trainer-ops#111), and the rebuild, which
    # keeps done days as they are.
    plan = await _mark_done_sessions(db, user, plan)
    # A plan built from outdated inputs is rebuilt whole, once, instead of
    # adapted (ai-trainer-ops#206): the rebuild already plans the coming days.
    if await rebuild_if_inputs_changed(db, user, now=now, timezone_name=timezone_name):
        await _translate_for_the_reader(db, user, today)
        return True
    trained = await _trained_dates(db, user.id, plan, today)
    # No reason, no run — and therefore no message. The old condition was true
    # for the rest of the plan's life once any single day had been missed, which
    # is why this fired on 30 nights out of 30 (#666).
    reasons = maintenance_reasons(plan, today, trained_dates=trained)
    if await _week_targets_moved(db, user, plan, date.fromisoformat(today)):
        reasons.append("week_targets_moved")
    if not reasons:
        return False

    before_row = await crud.get_training_plan(db, user.id)
    before_plan = before_row.plan if before_row is not None else []
    async with track_llm_usage(db, user, source="step:plan-maintenance"):
        commit = await adapt_plan(
            db,
            user,
            plan,
            feedback=[],
            source="nightly_maintenance",
            now=now,
            timezone_name=timezone_name,
        )
    if commit is None:
        logger.info(
            "Daily plan maintenance found nothing to change user_id=%s reasons=%s",
            user.id,
            ",".join(reasons),
        )
        return False

    changed = commit.plan != before_plan
    if changed:
        await _translate_for_the_reader(db, user, today)
    return changed


async def _translate_for_the_reader(
    db: AsyncSession, user: models.User, today: str
) -> None:
    """Write the night's new plan texts in the language the athlete last read
    the app in, so the morning's first view is not English until the page asks
    (ai-trainer-ops#271). Best-effort, like the page's own write."""
    language = user.interface_language
    if language and language in plan_translations.LANGUAGE_CODES:
        await plan_translations.translate_new_plan(
            db,
            user,
            language,
            date.fromisoformat(today),
            source="step:plan-translations",
        )


# Run for every athlete with a plan by the shared loop (ai-trainer-ops#252). The
# step is looked up when the job runs, so a test can replace it.
JOB = PerUserJob(
    name="daily-plan-maintenance",
    label="Daily plan maintenance",
    step=lambda db, user, *, now, timezone_name: maintain_user_training_plan(
        db,
        user,
        now=now,
        # The athlete's day: at 02:00 in Berlin it is 20:00 in New York, and
        # that evening's session is not missed yet (ai-trainer-ops#128).
        timezone_name=user.timezone_name or timezone_name,
    ),
    counts=("updated",),
    hour=DAILY_PLAN_MAINTENANCE_HOUR,
)
run_daily_plan_maintenance = JOB.run
