"""Helpers for matching imported rides to scheduled training-plan days."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services import ai_service
from services import coach_summary
from services.activity_identity import activities_form_one_session
from services.analysis import build_ride_analysis, compare_planned_vs_actual
from services.dates import app_today_iso
from services.duration_range import duration_range
from services import plan_pipeline

logger = logging.getLogger(__name__)

MATCH_UNMATCHED = "unmatched"
MATCH_AUTO = "auto_matched"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_MANUAL = "manual_matched"

LABEL_ADDITIONAL = "Additional"
LABEL_OK = "OK"
LABEL_TOO_MUCH = "Too much"
LABEL_MISMATCH = "Mismatch"

# Labels produced by automatic ride↔plan matching. These are recomputed live on
# every match pass, so they may be freely overwritten. Any *other* non-null label
# (subjective rider feedback like "Solid"/"Close"/"Off plan", or an explicit coach
# label) is sticky and must survive re-matching.
AUTO_LABELS = frozenset({LABEL_ADDITIONAL, LABEL_OK, LABEL_TOO_MUCH, LABEL_MISMATCH})

COMBINED_DURATION_MIN_RATIO = 0.8
COMBINED_DURATION_MAX_RATIO = 1.25
# An easy session is judged on whether the movement happened, not on hitting a
# duration. Two thirds of a planned recovery spin is still a recovery spin.
EASY_COMBINED_DURATION_MIN_RATIO = 0.6
EASY_COMBINED_DURATION_MAX_RATIO = 1.5

# How far apart two recordings may sit and still read as one interrupted
# session. Deliberately not one number, because "one session" means different
# things depending on what the session was *for*.
#
# For an easy or recovery session, continuity is not the stimulus. Blood flow,
# metabolite clearance and low-load movement do not care whether the hour came
# in one piece — and the evidence that active recovery beats plain rest at all
# is thin and mixed. Being pedantic about how a low-stakes intervention was
# delivered is precision the underlying effect does not support, so the window
# is effectively the whole day.
#
# For an endurance session, continuity *is* the stimulus. A long ride
# progressively depletes glycogen — itself a signal for mitochondrial
# adaptation — shifts substrate use toward fat, and trains durability: holding
# power after accumulated work, which is a distinct quality that a fresh-state
# FTP does not predict. Two fresh hours never put the athlete in the state the
# adaptation comes from. So this window is *tighter* than the old global 90
# minutes, not looser: what it exists to forgive is a café stop or a battery
# swap, and a real café stop is half an hour. Ninety minutes is long enough to
# eat, shower and start again, which is a second outing rather than an
# interrupted ride.
#
# Structured hard sessions never get here — ``_is_duration_focused_ride_plan``
# excludes them, because whether 4x8 at threshold happened is not a question any
# duration sum can answer.
EASY_SPLIT_SESSION_MAX_GAP_SECONDS = 8 * 60 * 60
ENDURANCE_SPLIT_SESSION_MAX_GAP_SECONDS = 45 * 60

# What marks a planned session as one where continuity does not matter.
#
# Bare "easy" is deliberately absent. Plenty of endurance plans describe
# themselves as easy aerobic riding, and treating those as recovery would hand a
# long ride the whole-day window — the exact false positive this distinction
# exists to avoid. The markers here name the *purpose*, not the effort.
_EASY_PLAN_MARKERS = (
    "recovery",
    "regeneration",
    "shakeout",
    "yoga",
    "mobility",
    "stretch",
)
HARD_EXTRA_INTENSITY_FACTOR = 0.82
HARD_EXTRA_TSS = 85.0


def _is_training_day(day: dict | None) -> bool:
    if not day:
        return False
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "").lower()
    duration = day.get("durationMinutes") or day.get("duration_minutes") or 0
    return workout_type not in {"", "rest"} and int(duration or 0) > 0


def _group_sessions_by_date(
    plan: list[dict] | None, *, training_only: bool
) -> dict[str, list[dict]]:
    """Group a plan's sessions by date, each date's list ordered by slot (#496).

    A date may hold several sessions (AM yoga + PM endurance), so grouping — not
    the old ``{date: day}`` map, which silently kept whichever session came last —
    is what lets each executed activity attach to its own planned session.
    """
    grouped: dict[str, list[dict]] = {}
    for day in plan or []:
        if not isinstance(day, dict) or not day.get("date"):
            continue
        if training_only and not _is_training_day(day):
            continue
        grouped.setdefault(str(day["date"]), []).append(day)
    for sessions in grouped.values():
        sessions.sort(key=schemas.day_slot)
    return grouped


def _plan_days_by_date(plan: list[dict] | None) -> dict[str, dict]:
    """The *first* trainable session per date.

    Retained for callers that legitimately want one representative session for a
    date (manual match resolution, legacy single-session paths).
    """
    return {
        date: sessions[0]
        for date, sessions in _group_sessions_by_date(
            plan, training_only=True
        ).items()
    }


def _all_plan_days_by_date(plan: list[dict] | None) -> dict[str, dict]:
    return {
        date: sessions[0]
        for date, sessions in _group_sessions_by_date(
            plan, training_only=False
        ).items()
    }


# Sport-type fragments that mark an executed activity as a gym/mobility session
# rather than a ride, so a planned strength session and a planned ride on the same
# date each pull the activity that actually belongs to them.
_STRENGTH_SPORT_MARKERS = (
    "weight",
    "strength",
    "workout",
    "gym",
    "crossfit",
    "yoga",
    "pilates",
    "core",
)


def _is_strength_activity(ride: models.RideMetric) -> bool:
    sport_type = str(ride.sport_type or "").lower()
    return any(marker in sport_type for marker in _STRENGTH_SPORT_MARKERS)


def _session_accepts_ride(session: dict, ride: models.RideMetric) -> bool:
    """Whether ``ride``'s sport is plausible for this planned session.

    Deliberately permissive: it only rules out the two combinations that are
    clearly wrong (a gym activity against a planned ride, a ride against a
    planned strength session). Everything else stays eligible and is decided on
    duration, so an unusual sport type never leaves a session unmatched.
    """
    workout_type = str(
        session.get("workoutType") or session.get("workout_type") or ""
    ).lower()
    if workout_type == "strength":
        return _is_strength_activity(ride)
    if _is_strength_activity(ride):
        return False
    return True


def _session_duration_cost(session: dict, ride: models.RideMetric) -> float:
    """How far ``ride``'s duration sits outside ``session``'s planned window.

    Zero inside the window (on target), otherwise the distance to the nearest
    bound. Rides or sessions with no usable duration score a flat mid cost so
    they neither win nor are excluded outright.
    """
    actual = _ride_duration_minutes(ride)
    lo, hi = duration_range(session)
    if actual is None or lo is None or hi is None:
        return 60.0
    if lo <= actual <= hi:
        return 0.0
    return float(min(abs(actual - lo), abs(actual - hi)))


def _ride_start_order(ride: models.RideMetric) -> str:
    """Sort key putting the day's earlier activity first.

    ``activity_start_datetime`` is an ISO string, so lexical order is chronological
    order. Rides without one sort last but stay deterministic via the activity id.
    """
    return str(ride.activity_start_datetime or "~") + f"#{ride.strava_activity_id}"


def _assign_rides_to_sessions(
    sessions: list[dict], rides: list[models.RideMetric]
) -> dict[int, dict]:
    """Map each ride's activity id to the planned session it best fits.

    Greedy over the globally cheapest eligible (ride, session) pair, so the
    morning gym activity and the evening ride each land on their own session
    rather than competing for one day (#496). Each session takes at most one ride
    and each ride at most one session; leftovers stay unmatched and are labelled
    as extras by the caller.

    Ties break on chronological order — the earlier activity takes the earlier
    slot — which is what makes "AM yoga, PM endurance" resolve the obvious way
    when both sessions fit equally well.
    """
    ordered_rides = sorted(rides, key=_ride_start_order)
    ride_order = {
        ride.strava_activity_id: index for index, ride in enumerate(ordered_rides)
    }
    candidates: list[tuple[float, int, int, int]] = []
    for session_index, session in enumerate(sessions):
        for ride in ordered_rides:
            if not _session_accepts_ride(session, ride):
                continue
            cost = _session_duration_cost(session, ride)
            # Chronological agreement is a tiebreak, never a driver: it only
            # separates candidates whose duration fit is equally good.
            order_penalty = abs(ride_order[ride.strava_activity_id] - session_index)
            candidates.append(
                (cost, order_penalty, session_index, ride.strava_activity_id)
            )
    candidates.sort()
    assigned: dict[int, dict] = {}
    used_sessions: set[int] = set()
    for _cost, _penalty, session_index, activity_id in candidates:
        if session_index in used_sessions or activity_id in assigned:
            continue
        used_sessions.add(session_index)
        assigned[activity_id] = sessions[session_index]
    return assigned


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _plan_duration_minutes(day: dict | None) -> int | None:
    if not isinstance(day, dict):
        return None
    duration = day.get("durationMinutes") or day.get("duration_minutes")
    try:
        duration_min = int(duration or 0)
    except (TypeError, ValueError):
        return None
    return duration_min if duration_min > 0 else None


def _ride_duration_minutes(ride: models.RideMetric) -> int | None:
    if not ride.duration_seconds:
        return None
    return max(1, round(ride.duration_seconds / 60))


def _duration_ratio(
    duration_min: int | None,
    plan_duration_min: int | None,
) -> float | None:
    if not duration_min or not plan_duration_min:
        return None
    return duration_min / plan_duration_min


def _plan_duration_range(day: dict | None) -> tuple[int | None, int | None]:
    """The planned duration window ``(lo, hi)`` for ``day`` (#368)."""
    return duration_range(day)


def _duration_mismatch_label(
    duration_min: int | None,
    plan_duration_min: int | None,
    plan_range: tuple[int | None, int | None] = (None, None),
) -> str | None:
    lo, hi = plan_range
    # A prescribed window makes any actual inside it on-target — never a mismatch.
    if duration_min is not None and lo is not None and hi is not None and lo <= duration_min <= hi:
        return None
    ratio = _duration_ratio(duration_min, plan_duration_min)
    if ratio is not None and (ratio > 2.5 or ratio < 0.3):
        return LABEL_MISMATCH
    return None


def _resolve_label(ride: models.RideMetric, auto_label: str | None) -> str | None:
    """Decide the label to persist when (re-)matching ``ride``.

    Automatic match labels are recomputed on every pass and reflect the *current*
    ride and the *current* matched plan day, so they stay live. Subjective rider
    feedback and explicit coach labels are sticky: they are preserved and never
    clobbered by a re-match (which would otherwise reset them — often to ``None``
    — on the next page reload).
    """
    existing = ride.label_override
    if existing is not None and existing not in AUTO_LABELS:
        return existing
    return auto_label


def _is_easy_plan(day: dict) -> bool:
    """Is this a session whose point is movement rather than accumulated work?

    Read from the plan rather than from what the athlete actually did: the
    question is what the session was *for*, and only the plan says that. The
    production case that prompted this read ``workoutType: "endurance"`` with the
    title "Short Heat-Friendly Recovery Spin", so the text has to be searched and
    not just the type.
    """
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "").lower()
    title = str(day.get("title") or "").lower()
    description = str(day.get("description") or "").lower()
    text = " ".join([workout_type, title, description])
    return any(marker in text for marker in _EASY_PLAN_MARKERS)


def _split_session_max_gap(day: dict) -> int:
    """The gap two recordings may span and still count as one planned session."""
    if _is_easy_plan(day):
        return EASY_SPLIT_SESSION_MAX_GAP_SECONDS
    return ENDURANCE_SPLIT_SESSION_MAX_GAP_SECONDS


def _combined_duration_ratios(day: dict) -> tuple[float, float]:
    if _is_easy_plan(day):
        return EASY_COMBINED_DURATION_MIN_RATIO, EASY_COMBINED_DURATION_MAX_RATIO
    return COMBINED_DURATION_MIN_RATIO, COMBINED_DURATION_MAX_RATIO


def _is_structured_hard_plan(day: dict) -> bool:
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "").lower()
    title = str(day.get("title") or "").lower()
    description = str(day.get("description") or "").lower()
    text = " ".join([workout_type, title, description])
    hard_markers = (
        "interval",
        "vo2",
        "threshold",
        "anaerobic",
        "sprint",
        "hiit",
        "race",
    )
    return any(marker in text for marker in hard_markers)


def _is_duration_focused_ride_plan(day: dict, rides: list[models.RideMetric]) -> bool:
    if _plan_duration_minutes(day) is None or _is_structured_hard_plan(day):
        return False
    return all(_is_cycling_ride(ride) for ride in rides)


def _is_cycling_ride(ride: models.RideMetric) -> bool:
    sport_type = str(ride.sport_type or "").lower()
    return "ride" in sport_type or "cycling" in sport_type or "bike" in sport_type


def _rides_are_one_split_session(
    rides: list[models.RideMetric],
    *,
    # Required, not defaulted: there is no longer one right window, so a caller
    # that has not decided which session this is has not decided the question.
    max_gap_seconds: int,
) -> bool:
    """Do these recordings look like one session that was saved in parts?

    Adding two rides' durations together only says something about a planned
    session if they were one ride to begin with. Same sport and no meaningful
    gap between them is what makes that plausible; a morning commute and an
    evening ride fail it despite both being cycling (#543).

    What counts as "no meaningful gap" depends on the session — see
    :func:`_split_session_max_gap`.
    """
    return activities_form_one_session(
        [
            (ride.sport_type, ride.activity_start_datetime, ride.duration_seconds)
            for ride in rides
        ],
        max_gap_seconds=max_gap_seconds,
    )


def _combined_duration_matches_plan(
    rides: list[models.RideMetric],
    plan_duration_min: int,
    *,
    min_ratio: float = COMBINED_DURATION_MIN_RATIO,
    max_ratio: float = COMBINED_DURATION_MAX_RATIO,
) -> bool:
    durations = [_ride_duration_minutes(ride) for ride in rides]
    if any(duration is None for duration in durations):
        return False
    total_min = sum(duration or 0 for duration in durations)
    ratio = total_min / plan_duration_min
    return min_ratio <= ratio <= max_ratio


def _best_matching_ride(
    rides: list[models.RideMetric],
    plan_duration_min: int | None,
) -> models.RideMetric:
    if plan_duration_min is None:
        return max(rides, key=lambda ride: _ride_duration_minutes(ride) or 0)
    return min(
        rides,
        key=lambda ride: (
            abs((_ride_duration_minutes(ride) or 0) - plan_duration_min),
            -(_ride_duration_minutes(ride) or 0),
        ),
    )


def _is_hard_extra_ride(ride: models.RideMetric) -> bool:
    if (
        ride.intensity_factor is not None
        and ride.intensity_factor >= HARD_EXTRA_INTENSITY_FACTOR
    ):
        return True
    if ride.tss is not None and ride.tss >= HARD_EXTRA_TSS:
        return True
    if (
        ride.normalized_power_w is not None
        and ride.ftp_used is not None
        and ride.ftp_used > 0
        and ride.normalized_power_w / ride.ftp_used >= HARD_EXTRA_INTENSITY_FACTOR
    ):
        return True
    purpose = str(ride.ride_purpose or "").lower()
    return any(
        marker in purpose
        for marker in ("interval", "vo2", "threshold", "anaerobic", "sprint", "race")
    )


def _ride_feedback_from_metric(ride: models.RideMetric) -> dict[str, Any]:
    feedback: dict[str, Any] = {}
    if ride.duration_seconds:
        feedback["actualDurationMinutes"] = max(1, round(ride.duration_seconds / 60))
    if ride.avg_power_w:
        feedback["averagePower"] = ride.avg_power_w
    if ride.user_note:
        feedback["notes"] = ride.user_note
        match = re.search(r"RPE\s+(\d+)\s*/\s*10", ride.user_note, flags=re.IGNORECASE)
        if match:
            # rate_completed_workout's older prompt uses a 1-5 effort scale.
            feedback["perceivedEffort"] = max(1, min(5, round(int(match.group(1)) / 2)))
    # No default RPE: when the athlete never reported perceived effort, leave it
    # absent so downstream prompts omit it entirely. A hardcoded "3/5" here was
    # surfaced to the athlete as if they had reported it (see login summary).
    return feedback


def _session_feedback_from_metrics(
    rides: list[models.RideMetric],
) -> dict[str, Any]:
    """What the athlete did in this session, across every recording of it.

    A session split across two files is matched as two rides on one slot (#543).
    Describing it by one of them told the athlete they had done half of what
    they did, which is the opposite of what the match asserted (#545).

    Duration is the sum. Average power is weighted by duration, so it is the
    session's average and not the mean of two averages. Notes and perceived
    effort come from whichever recording the athlete actually commented on —
    they are a statement about the session, not about a file.
    """
    if not rides:
        return {}
    if len(rides) == 1:
        return _ride_feedback_from_metric(rides[0])

    commented = next((ride for ride in rides if ride.user_note), rides[0])
    feedback = _ride_feedback_from_metric(commented)

    total_seconds = sum(ride.duration_seconds or 0 for ride in rides)
    if total_seconds:
        feedback["actualDurationMinutes"] = max(1, round(total_seconds / 60))

    weighted = [
        (ride.avg_power_w, ride.duration_seconds)
        for ride in rides
        if ride.avg_power_w and ride.duration_seconds
    ]
    if weighted:
        feedback["averagePower"] = round(
            sum(power * seconds for power, seconds in weighted)
            / sum(seconds for _power, seconds in weighted)
        )
    return feedback


async def _session_group_rides(
    db: AsyncSession, user_id: str, ride: models.RideMetric
) -> list[models.RideMetric]:
    """Every recording matched to the same planned session as *ride*.

    ``apply_ride_plan_matches`` deliberately returns one representative ride per
    matched session — reviewing each half separately would mean two coach notes
    and two LLM calls for one session. The other halves are found here instead,
    from what the match already wrote to the rows.

    Looked up by the ride's own ``activity_date`` rather than by
    ``matched_plan_date``: a manual resolve can attach a ride to a planned date
    it was not recorded on, and the halves of a split session always share the
    date they were ridden.
    """
    slot = _matched_slot(ride)
    same_day = await crud.get_ride_metrics_by_date(db, user_id, ride.activity_date)
    group = [
        other
        for other in same_day
        if other.plan_match_status == MATCH_AUTO
        and str(other.matched_plan_date or "") == str(ride.matched_plan_date or "")
        and _matched_slot(other) == slot
    ]
    if not any(
        other.strava_activity_id == ride.strava_activity_id for other in group
    ):
        group.append(ride)
    return sorted(group, key=_ride_start_order)


# Canonical implementation lives in the shared pipeline; kept as an alias so the
# per-day merge logic never diverges between this module and the pipeline.
_apply_plan_updates = plan_pipeline.apply_plan_updates


def _matched_slot(ride: models.RideMetric) -> int:
    """The plan slot ``ride`` is matched to, as a total function.

    ``matched_plan_slot`` is NULL for every row written before two-a-days existed
    and for every ride matched to a single-session day; both mean slot 0.
    """
    return schemas.normalize_slot(getattr(ride, "matched_plan_slot", None))


def _session_at_slot(sessions: list[dict], slot: int | None) -> dict | None:
    """The session sitting at ``slot``, or ``None``.

    A ``None`` slot is a pre-#496 match against a single-session day, so it
    resolves to the date's first session rather than to nothing.
    """
    if not sessions:
        return None
    if slot is None:
        return sessions[0]
    wanted = schemas.normalize_slot(slot)
    return next((s for s in sessions if schemas.day_slot(s) == wanted), None)


async def _match_multi_session_date(
    db: AsyncSession,
    activity_date: str,
    sessions: list[dict],
    date_rides: list[models.RideMetric],
) -> list[models.RideMetric]:
    """Attach each of a two-a-day's rides to its own planned session (#496).

    With one planned session per date, a second activity could only ever end up
    ``MATCH_UNMATCHED`` — the data was stored but not attributable. Here every
    session takes at most one ride (chosen on sport plausibility then duration
    fit), so a morning gym session and an evening ride are each recorded against
    the session they belong to. Rides left over after every session is filled are
    genuine extras and keep the existing ``Additional`` / ``Too much`` labelling.
    """
    assigned = _assign_rides_to_sessions(sessions, date_rides)
    auto_matched: list[models.RideMetric] = []
    for ride in date_rides:
        session = assigned.get(ride.strava_activity_id)
        if session is None:
            await crud.update_ride_match(
                db,
                ride,
                # Unattributable to any one session, but the date *is* planned —
                # keep the date so the dashboard still shows it in context, and
                # leave slot/snapshot empty rather than implying a session.
                status=MATCH_UNMATCHED,
                matched_plan_date=activity_date,
                matched_plan_slot=None,
                matched_plan_snapshot=None,
                matched_at=None,
                label_override=_resolve_label(
                    ride,
                    LABEL_TOO_MUCH if _is_hard_extra_ride(ride) else LABEL_ADDITIONAL,
                ),
            )
            continue
        await crud.update_ride_match(
            db,
            ride,
            status=MATCH_AUTO,
            matched_plan_date=activity_date,
            matched_plan_slot=schemas.day_slot(session),
            matched_plan_snapshot=session,
            matched_at=_utcnow(),
            label_override=_resolve_label(
                ride,
                _duration_mismatch_label(
                    _ride_duration_minutes(ride),
                    _plan_duration_minutes(session),
                    _plan_duration_range(session),
                ),
            ),
        )
        auto_matched.append(ride)
    return auto_matched


async def apply_ride_plan_matches(
    db: AsyncSession,
    user_id: str,
    plan: list[dict] | None,
    strava_activity_ids: list[int],
) -> list[models.RideMetric]:
    """Apply date-based plan matching for newly inserted rides.

    Returns the rides that were automatically matched and can be reviewed
    without asking the athlete to disambiguate.
    """
    rides = await crud.get_ride_metrics_by_activity_ids(db, user_id, strava_activity_ids)
    if not rides:
        return []

    sessions_by_date = _group_sessions_by_date(plan, training_only=True)
    display_sessions_by_date = _group_sessions_by_date(plan, training_only=False)
    auto_matched: list[models.RideMetric] = []

    for activity_date in sorted({ride.activity_date for ride in rides}):
        day_sessions = sessions_by_date.get(activity_date, [])
        plan_day = day_sessions[0] if day_sessions else None
        display_sessions = display_sessions_by_date.get(activity_date, [])
        display_plan_day = display_sessions[0] if display_sessions else None
        date_rides = await crud.get_ride_metrics_by_date(db, user_id, activity_date)
        if not date_rides:
            continue

        manual_match = next(
            (
                ride
                for ride in date_rides
                if ride.plan_match_status == MATCH_MANUAL
                and ride.matched_plan_date == activity_date
            ),
            None,
        )
        if manual_match is not None:
            # The athlete's own attribution wins, including which session they
            # pointed the ride at — re-matching must never silently move it.
            manual_session = _session_at_slot(
                display_sessions, manual_match.matched_plan_slot
            ) or display_plan_day
            for ride in date_rides:
                if ride.strava_activity_id == manual_match.strava_activity_id:
                    await crud.update_ride_match(
                        db,
                        ride,
                        status=MATCH_MANUAL,
                        matched_plan_date=activity_date if manual_session else None,
                        matched_plan_slot=(
                            schemas.day_slot(manual_session) if manual_session else None
                        ),
                        matched_plan_snapshot=manual_session,
                        matched_at=ride.matched_at or _utcnow(),
                        label_override=_resolve_label(ride, None),
                    )
                else:
                    await crud.update_ride_match(
                        db,
                        ride,
                        status=MATCH_UNMATCHED,
                        matched_plan_date=activity_date if display_plan_day else None,
                        matched_plan_snapshot=display_plan_day,
                        label_override=_resolve_label(ride, None),
                    )
            continue

        if len(day_sessions) > 1:
            auto_matched.extend(
                await _match_multi_session_date(
                    db, activity_date, day_sessions, date_rides
                )
            )
            continue

        if plan_day is None:
            for ride in date_rides:
                await crud.update_ride_match(
                    db,
                    ride,
                    status=MATCH_UNMATCHED,
                    matched_plan_date=activity_date if display_plan_day else None,
                    matched_plan_snapshot=display_plan_day,
                    label_override=_resolve_label(ride, None),
                )
            continue

        # Which of the day's activities could plausibly be *this* session at
        # all. The guard already existed and is what the two-a-day path uses
        # (#496); the single-session path never asked, so one yoga class on a
        # cycling day made `_is_duration_focused_ride_plan` false for the whole
        # day — `all(_is_cycling_ride(...))` over a list containing it — and
        # dropped every activity into `ambiguous`. On 2026-08-09 that left two
        # road rides and a yoga session all asking the athlete which one was the
        # planned recovery spin.
        candidates = [ride for ride in date_rides if _session_accepts_ride(plan_day, ride)]
        other_sports = [
            ride
            for ride in date_rides
            if not any(ride.id == candidate.id for candidate in candidates)
        ]
        for ride in other_sports:
            # Extra, not late and not excessive. It was never a candidate for
            # this session, so it is not linked to it and carries no label about
            # it — a yoga class must not read as a failed bike ride.
            await crud.update_ride_match(
                db,
                ride,
                status=MATCH_UNMATCHED,
                matched_plan_date=None,
                matched_plan_snapshot=None,
                matched_at=None,
                label_override=_resolve_label(ride, None),
            )
        if not candidates:
            continue

        if len(candidates) == 1:
            ride = candidates[0]
            plan_duration_min = _plan_duration_minutes(plan_day)
            label_override = _duration_mismatch_label(
                _ride_duration_minutes(ride),
                plan_duration_min,
                _plan_duration_range(plan_day),
            )
            await crud.update_ride_match(
                db,
                ride,
                status=MATCH_AUTO,
                matched_plan_date=activity_date,
                matched_plan_slot=schemas.day_slot(plan_day),
                matched_plan_snapshot=plan_day,
                matched_at=_utcnow(),
                label_override=_resolve_label(ride, label_override),
            )
            auto_matched.append(ride)
        else:
            plan_duration_min = _plan_duration_minutes(plan_day)
            if (
                _is_duration_focused_ride_plan(plan_day, candidates)
                and plan_duration_min
            ):
                best_match = _best_matching_ride(candidates, plan_duration_min)
                # Both conditions, in this order: the durations may only be
                # added up once the recordings have been shown to be parts of
                # one session. A sum that happens to fit the plan is not
                # evidence of anything on its own — a commute plus an evening
                # ride reached exactly that sum and completed the day (#543).
                min_ratio, max_ratio = _combined_duration_ratios(plan_day)
                combined_matches = _rides_are_one_split_session(
                    candidates,
                    max_gap_seconds=_split_session_max_gap(plan_day),
                ) and _combined_duration_matches_plan(
                    candidates,
                    plan_duration_min,
                    min_ratio=min_ratio,
                    max_ratio=max_ratio,
                )

                for ride in candidates:
                    if combined_matches:
                        await crud.update_ride_match(
                            db,
                            ride,
                            status=MATCH_AUTO,
                            matched_plan_date=activity_date,
                            matched_plan_slot=schemas.day_slot(plan_day),
                            matched_plan_snapshot=plan_day,
                            matched_at=_utcnow(),
                            label_override=_resolve_label(ride, LABEL_OK),
                        )
                    elif ride.strava_activity_id == best_match.strava_activity_id:
                        await crud.update_ride_match(
                            db,
                            ride,
                            status=MATCH_AUTO,
                            matched_plan_date=activity_date,
                            matched_plan_slot=schemas.day_slot(plan_day),
                            matched_plan_snapshot=plan_day,
                            matched_at=_utcnow(),
                            label_override=_resolve_label(
                                ride,
                                _duration_mismatch_label(
                                    _ride_duration_minutes(ride),
                                    plan_duration_min,
                                    _plan_duration_range(plan_day),
                                ),
                            ),
                        )
                    else:
                        await crud.update_ride_match(
                            db,
                            ride,
                            status=MATCH_UNMATCHED,
                            matched_plan_date=activity_date,
                            matched_plan_snapshot=plan_day,
                            matched_at=None,
                            label_override=_resolve_label(
                                ride,
                                LABEL_TOO_MUCH
                                if _is_hard_extra_ride(ride)
                                else LABEL_ADDITIONAL,
                            ),
                        )
                # One entry per matched *session*, not per matched ride: when a
                # split session put two rides on this slot, returning both would
                # buy two coach notes and two LLM calls for one session. The
                # other halves are recovered by _session_group_rides, so the
                # review still sees the whole session (#545).
                auto_matched.append(best_match)
            else:
                for ride in candidates:
                    await crud.update_ride_match(
                        db,
                        ride,
                        status=MATCH_AMBIGUOUS,
                        matched_plan_date=activity_date,
                        matched_plan_snapshot=plan_day,
                        matched_at=None,
                        label_override=_resolve_label(ride, None),
                    )

    return auto_matched


async def mark_matched_days_completed(
    db: AsyncSession,
    user: models.User,
    plan: list[dict] | None,
    rides: list[models.RideMetric],
    *,
    source: str = "activity_import",
) -> None:
    """Mark each ride's matched plan *session* completed via the plan pipeline.

    A synced activity is proof the athlete did that session, but nothing else sets
    the plan-day ``completed`` flag — it is otherwise only set when the athlete ticks
    a workout by hand. Setting it here makes completed-day protection and the
    dashboard's completed state work for matched rides without a manual tick.
    Idempotent: already-completed sessions (and sessions the pipeline's pin guard
    owns) are left untouched.

    Manual matches count for the same reason auto matches do, and more strongly:
    the athlete answering "that ride was the evening intervals" is the best evidence
    a session was ridden that this system will ever get. Leaving them out meant a
    resolved ambiguity never marked its session done, so the calendar kept showing
    an untouched day and an automated regenerate could still drop it (#574).

    Completion is per session (#496): the morning gym ride marks the AM session done
    and leaves the evening endurance session pending, instead of ticking the date.
    """
    if not plan:
        return
    plan_by_key = {
        schemas.session_key(d): d
        for d in plan
        if isinstance(d, dict) and d.get("date")
    }
    matched_keys = sorted(
        {
            (ride.matched_plan_date, _matched_slot(ride))
            for ride in rides
            if ride.matched_plan_date
            and ride.plan_match_status in {MATCH_AUTO, MATCH_MANUAL}
        }
    )
    updates = [
        {"date": date, "slot": slot, "completed": True}
        for date, slot in matched_keys
        if plan_by_key.get((date, slot))
        and not plan_by_key[(date, slot)].get("completed")
    ]
    if not updates:
        return
    await plan_pipeline.commit_plan_updates(
        db, user, updates, base_plan=plan, source=source
    )


async def refresh_matches_for_dates(
    db: AsyncSession,
    user_id: str,
    plan: list[dict] | None,
    dates: list[str],
) -> list[models.RideMetric]:
    """Re-run plan matching for every ride on ``dates`` against the current plan.

    Snapshots (``matched_plan_snapshot`` / ``matched_plan_date``) are captured
    only at ride-import time, so a ride already imported for a date keeps a stale
    (or NULL) snapshot when the plan for that date later changes. This refreshes
    those rides from the current plan — driven by the plan-change pipeline — so a
    completed ride keeps the latest planned workout *before* the day rolls off the
    rolling window and the dashboard falls back to the snapshot (#364).
    """
    activity_ids: list[int] = []
    for activity_date in dates:
        rides = await crud.get_ride_metrics_by_date(db, user_id, activity_date)
        activity_ids.extend(ride.strava_activity_id for ride in rides)
    if not activity_ids:
        return []
    return await apply_ride_plan_matches(db, user_id, plan, activity_ids)


def _select_ride(
    rides: list[models.RideMetric],
    strava_activity_id: int,
    external_activity_id: str | None,
) -> models.RideMetric | None:
    """Find the ride the athlete pointed at, precision-safely.

    Non-Strava activities carry a synthesized 63-bit ``strava_activity_id``.
    JavaScript numbers are float64, so anything above 2^53 is rounded on the way
    through the browser: ``7846148097020609552`` comes back as
    ``...609536`` and matches no row (#441). Every other write path from an
    activity card already sends the provider's own string id for exactly this
    reason; this one did not, so resolving an ambiguous intervals.icu ride always
    404'd and the card said "Could not save that".

    The numeric id stays the fallback: Strava's own ids are well inside float64
    and older clients do not send the string.
    """
    if external_activity_id:
        match = next(
            (r for r in rides if r.external_activity_id == external_activity_id),
            None,
        )
        if match is not None:
            return match
    return next(
        (r for r in rides if r.strava_activity_id == strava_activity_id), None
    )


async def resolve_manual_match(
    db: AsyncSession,
    user_id: str,
    *,
    planned_date: str,
    strava_activity_id: int,
    plan: list[dict] | None,
    planned_slot: int | None = None,
    external_activity_id: str | None = None,
) -> models.RideMetric | None:
    """Resolve an ambiguous date by marking one ride as the planned workout.

    ``planned_slot`` picks which session on ``planned_date`` the athlete meant when
    the day holds more than one (#496); omitting it selects the day's first session,
    which is the only one a single-session day has.

    ``external_activity_id`` is the precision-safe identity; see
    :func:`_select_ride`.
    """
    day_sessions = _group_sessions_by_date(plan, training_only=True).get(
        planned_date, []
    )
    plan_day = _session_at_slot(day_sessions, planned_slot)
    if plan_day is None:
        return None

    rides = await crud.get_ride_metrics_by_date(db, user_id, planned_date)
    selected = _select_ride(rides, strava_activity_id, external_activity_id)
    if selected is None:
        return None

    target_slot = schemas.day_slot(plan_day)
    for ride in rides:
        # Compared by row identity rather than by the id that came off the wire:
        # once the ride is resolved, the corrupted number must not decide which
        # row gets written.
        if ride.id == selected.id:
            await crud.update_ride_match(
                db,
                ride,
                status=MATCH_MANUAL,
                matched_plan_date=planned_date,
                matched_plan_slot=target_slot,
                matched_plan_snapshot=plan_day,
                matched_at=_utcnow(),
                label_override=_resolve_label(ride, None),
            )
        elif _competes_for_session(ride, planned_date, target_slot):
            await crud.update_ride_match(
                db,
                ride,
                status=MATCH_UNMATCHED,
                label_override=_resolve_label(ride, None),
            )

    return selected


def _competes_for_session(
    ride: models.RideMetric, planned_date: str, slot: int
) -> bool:
    """Would *ride* still claim the session the athlete just assigned elsewhere?

    Only those get unmatched. This used to unmatch every other ride of the date,
    which was invisible while a resolve could only ever target the day's first
    session — but ambiguity is most likely on a two-a-day, so answering "the
    evening one was the intervals" would have thrown away the morning ride's
    perfectly good match (#547). An extra activity keeps its label too: it was
    never claiming this session.
    """
    if str(ride.plan_match_status or "") == MATCH_AMBIGUOUS:
        return str(ride.matched_plan_date or "") == planned_date
    if str(ride.plan_match_status or "") in {MATCH_AUTO, MATCH_MANUAL}:
        return (
            str(ride.matched_plan_date or "") == planned_date
            and _matched_slot(ride) == slot
        )
    return False


async def review_matched_ride_and_adapt(
    db: AsyncSession,
    user: models.User,
    ride: models.RideMetric,
    plan: list[dict],
    *,
    provider: str,
    streams: dict | None = None,
) -> tuple[str | None, list[dict] | None]:
    """Generate coach feedback for a matched ride and apply next-plan updates.

    The function is intentionally best-effort: failures are logged and surfaced
    as empty results so Strava import/feedback saving does not fail because an
    LLM or stream fetch was unavailable.
    """
    plan_day = ride.matched_plan_snapshot
    if not isinstance(plan_day, dict) and ride.matched_plan_date:
        # Fall back to the live plan, resolving the ride's own session rather than
        # whichever entry for that date happens to come first (#496).
        plan_day = _session_at_slot(
            sorted(
                (
                    day
                    for day in plan
                    if isinstance(day, dict)
                    and day.get("date") == ride.matched_plan_date
                ),
                key=schemas.day_slot,
            ),
            _matched_slot(ride),
        )
    if not isinstance(plan_day, dict):
        return None, None

    profile = schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True)
    ftp = float(current_ftp) if (current_ftp := (user.current_ftp or 0)) else 0.0
    if ftp <= 0 and user.rider_assessment is not None and user.rider_assessment.estimated_ftp:
        ftp = float(user.rider_assessment.estimated_ftp)

    # The session may have been recorded in several files (#543). What the coach
    # is told the athlete did has to be the session, not the file that happened
    # to be handed over; the stream analysis below stays scoped to that one
    # recording, because that is what its streams describe.
    session_rides = await _session_group_rides(db, user.id, ride)
    day_for_rating = dict(plan_day)
    day_for_rating["feedback"] = _session_feedback_from_metrics(session_rides)
    stream_delta = None
    ride_analysis = None
    if streams and ftp > 0:
        try:
            stream_delta = compare_planned_vs_actual(day_for_rating, streams, ftp=ftp)
            ride_analysis = build_ride_analysis(streams, ftp)
        except Exception:
            logger.warning("Failed to build planned-vs-actual stream context", exc_info=True)

    coach_note: str | None = None
    try:
        result = await ai_service.rate_completed_workout(
            day_for_rating,
            profile,
            provider=provider,
            stream_delta=stream_delta,
            ride_analysis=ride_analysis,
        )
        coach_note = result.get("feedback") or None
        if coach_note:
            await crud.update_ride_metric_notes(
                db,
                user.id,
                ride.strava_activity_id,
                coach_note=coach_note,
            )
            ride.coach_note = coach_note
    except Exception:
        logger.warning("Matched ride coach review failed", exc_info=True)

    plan_updates: list[dict] | None = None
    try:
        rider_assessment = None
        if user.rider_assessment is not None:
            rider_assessment = schemas.RiderAssessmentSchema.model_validate(
                user.rider_assessment, from_attributes=True
            ).model_dump(by_alias=True)
        coach_memory_row = await crud.get_coach_memory(db, user.id)
        coach_memory = coach_memory_row.memory if coach_memory_row is not None else ""
        athlete_context_row = await crud.get_athlete_context(db, user.id)
        athlete_context = (
            schemas.AthleteContextSchema.model_validate(
                athlete_context_row, from_attributes=True
            ).model_dump(by_alias=True)
            if athlete_context_row is not None
            else None
        )
        athlete_memory_fact_rows = await crud.get_prompt_athlete_memory_facts(
            db, user.id
        )
        athlete_memory_facts = [
            schemas.AthleteMemoryFactSchema.model_validate(
                fact, from_attributes=True
            ).model_dump(by_alias=True, mode="json")
            for fact in athlete_memory_fact_rows
        ]
        # Durable physiology/performance model (#384), gated on the athlete's
        # memory setting the same way routers/ai.py gates it for ask-trainer (#403).
        athlete_model_row = await crud.get_athlete_model(db, user.id)
        athlete_model = (
            schemas.AthleteModelSchema.model_validate(
                athlete_model_row, from_attributes=True
            ).model_dump(by_alias=True, mode="json")
            if (athlete_model_row is not None and user.memory_updates_enabled)
            else None
        )
        result = await ai_service.recommend_next_session(
            rides=session_rides,
            plan=plan,
            profile=profile,
            provider=provider,
            rider_assessment=rider_assessment,
            coach_memory=coach_memory,
            athlete_context=athlete_context,
            athlete_memory_facts=athlete_memory_facts,
            athlete_model=athlete_model,
            ctl=float(ride.ctl_after) if ride.ctl_after is not None else None,
            atl=float(ride.atl_after) if ride.atl_after is not None else None,
            tsb=float(ride.tsb_after) if ride.tsb_after is not None else None,
        )
        plan_updates = result.get("plan_updates") or None
        if plan_updates:
            # Never modify the session that was just matched — it belongs to the
            # completed ride. Only that session is off-limits: on a two-a-day the
            # coach may still retune the *other* session of the same date (#496).
            if ride.matched_plan_date:
                matched_key = (ride.matched_plan_date, _matched_slot(ride))
                plan_updates = [
                    u
                    for u in plan_updates
                    if (str(u.get("date") or ""), schemas.day_slot(u)) != matched_key
                ]
            plan_updates = plan_updates or None
        if plan_updates:
            # Constraint enforcement, completed-day protection, user-edit merge
            # and persistence are all owned by the shared pipeline.
            commit = await plan_pipeline.commit_plan_updates(
                db, user, plan_updates, base_plan=plan, source="ride_review"
            )
            await coach_summary.narrate_plan_changes(
                db,
                user,
                batch_id=commit.batch_id,
                source="ride_review",
                applied_changes=commit.applied_changes,
            )
    except Exception:
        logger.warning("Matched ride plan adaptation failed", exc_info=True)

    return coach_note, plan_updates
