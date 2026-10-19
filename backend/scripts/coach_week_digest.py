"""One athlete's week as the coach saw it, for the weekly coach review (ai-trainer-ops#143).

Run against a deployment's database, from ``backend/``:

    uv run python -m scripts.coach_week_digest --user-email a@b.c [--until YYYY-MM-DD] [--days 7]

The coach review in the ops repository hands this to a judge. For each coach
action of the week, the judge decides whether it fits what the athlete wrote,
what the athlete then rode, and what the coach itself said and changed. So
this prints, as one JSON document:

- ``plan``: every day from the start of the window to a week after it, in its
  last applied version as of the end of the window;
- ``events``, oldest first: the chat, with the messages just before the window
  as context; every plan change with its source and reason; the narratives of
  automated runs; rides; workout logs; and "This is wrong" flags;
- ``coach_actions``, what the judge judges: each reply in the chat, with the
  ``coach_chat`` batches it made, and each batch from a trigger the coach runs
  on its own, with its narrative where there is one.

The window is ``--days`` days ending on ``--until``, both inclusive, in the
account's time zone. ``--until`` defaults to yesterday there, so a Monday run
covers Monday to Sunday.

One account, named by its address. There is no aggregate mode, and nothing
about any other account is read. What judging does not need stays out: the
name, the e-mail and the user id, the start coordinates of rides, external
activity ids and provider metadata. Read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

import models
from database import async_session_maker
from scripts.plan_behaviour_report import final_plan

FORMAT = "coach-week-digest"
VERSION = 1
CONTEXT_MESSAGES = 6
PLAN_DAYS_AFTER = 7
AHEAD_DAYS = 14

# The plan-change triggers (services.plan_pipeline.PLAN_SOURCES), by what they
# are to the review. The test pins that every trigger is in exactly one of
# these, so a new trigger cannot drop out of the review unnoticed.
COACH_RUNS = frozenset(
    {"next_ride", "generate", "adapt", "auto_adapt", "nightly_maintenance", "ride_review"}
)
# Belongs to the reply it came with.
CHAT_SOURCE = "coach_chat"
# What the athlete did, or the sync recording it: context, never judged.
CONTEXT_SOURCES = frozenset({"user_edit", "activity_import", "manual_match", "workout_log"})


def _zone(name: str | None):
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _utc(value: datetime | None) -> datetime | None:
    """Stored times are UTC; SQLite hands them back without a zone."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_time(*candidates: str | None, zone) -> datetime | None:
    """The first ISO text that parses, read in the athlete's zone when it has none."""
    for text in candidates:
        try:
            parsed = datetime.fromisoformat(str(text))
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(timezone.utc)
    return None


def _squash(text: str | None) -> str:
    return " ".join((text or "").split())


def _event(event_id: str, at: datetime | None, kind: str, **fields) -> dict:
    return {"id": event_id, "at": at.isoformat() if at else None, "kind": kind, **fields}


def _message(message, narrates: str | None = None, context: bool = False) -> dict:
    event = _event(
        message.id,
        _utc(message.created_at),
        "message",
        role=message.role,
        text=message.content,
        plan_update_count=message.plan_update_count,
    )
    if narrates:
        event["narrates"] = narrates
    if context:
        event["context"] = True
    return event


def _change(change) -> dict:
    return _event(
        change.id,
        _utc(change.recorded_at),
        "plan_change",
        batch_id=change.batch_id,
        date=change.date,
        slot=change.slot,
        source=change.source,
        applied=change.applied,
        reason=change.reason,
        old=change.old_day,
        new=change.new_day,
    )


def _summary(summary) -> dict:
    return _event(
        summary.id,
        _utc(summary.recorded_at),
        "change_summary",
        batch_id=summary.batch_id,
        source=summary.source,
        text=summary.summary,
    )


def _ride(ride, zone) -> dict:
    return _event(
        ride.id,
        _local_time(ride.activity_start_datetime, ride.activity_date, zone=zone),
        "ride",
        date=str(ride.activity_date)[:10],
        sport=ride.sport_type,
        name=ride.activity_name,
        purpose=ride.ride_purpose,
        duration_min=round(ride.duration_seconds / 60) if ride.duration_seconds else None,
        distance_km=ride.distance_km,
        elevation_m=ride.elevation_gain_m,
        avg_power_w=ride.avg_power_w,
        np_w=ride.normalized_power_w,
        intensity_factor=ride.intensity_factor,
        tss=ride.tss,
        ftp_used=ride.ftp_used,
        avg_hr_bpm=ride.avg_heart_rate_bpm,
        ctl_after=ride.ctl_after,
        atl_after=ride.atl_after,
        tsb_after=ride.tsb_after,
        plan_match=ride.plan_match_status,
        matched_plan_date=ride.matched_plan_date,
        matched_plan_slot=ride.matched_plan_slot,
        match_label=ride.match_label,
        feel_legs=ride.feel_legs,
        athlete_note=ride.user_note,
        coach_note=ride.coach_note,
        weather={
            "temperature_c": ride.weather_temperature_c,
            "condition": ride.weather_condition,
            "wind_kph": ride.weather_wind_speed_kph,
            "precipitation_mm": ride.weather_precipitation_mm,
        },
    )


def _log(log, zone) -> dict:
    return _event(
        log.id,
        _local_time(log.completed_at, log.date, zone=zone),
        "workout_log",
        date=log.date,
        slot=log.slot,
        sport=log.sport_type,
        minutes=log.actual_duration_minutes,
        rpe=log.perceived_effort,
        avg_power_w=log.average_power,
        peak_power_w=log.peak_power,
        avg_hr_bpm=log.average_heart_rate,
        distance_km=log.distance_km,
        notes=log.notes,
    )


def flagged_reply(flag, messages) -> str | None:
    """The stored reply a "This is wrong" flag was tapped on, or None.

    The flag's ``ref`` is the timestamp the client showed, which a live reply
    does not share with its stored row (``models.WrongFlag``), so the text
    decides when the timestamp does not. The excerpt is the reply as shown, at
    most ``schemas.WRONG_FLAG_EXCERPT_MAX`` characters of it.
    """
    flagged_at = _utc(flag.created_at)
    earlier = [
        m for m in messages if m.role == "assistant" and _utc(m.created_at) <= flagged_at
    ]
    for message in reversed(earlier):
        if message.timestamp == flag.ref:
            return message.id
    excerpt = _squash(flag.excerpt)
    for message in reversed(earlier):
        if excerpt and _squash(message.content).startswith(excerpt):
            return message.id
    return None


def _flag(flag, messages) -> dict:
    return _event(
        flag.id,
        _utc(flag.created_at),
        "wrong_flag",
        target=flag.target,
        ref=flag.ref,
        excerpt=flag.excerpt,
        note=flag.note,
        message_id=flagged_reply(flag, messages) if flag.target == "reply" else None,
    )


def coach_actions(
    chat: list, window: list, changes: list, summaries: list
) -> tuple[list[dict], dict[str, str]]:
    """What the judge judges, oldest first, and which message narrates which batch.

    ``chat`` is every message the digest shows, context included, and
    ``window`` the ones inside the window. A narrated automated run posts its
    summary as a chat message too (``services.coach_summary``): that message is
    part of the run, not a reply of its own.
    """
    narrates: dict[str, str] = {}
    open_summaries = list(summaries)
    for message in window:
        if message.role != "assistant":
            continue
        same = [s for s in open_summaries if s.summary == message.content]
        if same:
            at = _utc(message.created_at)
            match = min(same, key=lambda s: abs(_utc(s.recorded_at) - at))
            narrates[message.id] = match.batch_id
            open_summaries.remove(match)

    batches: dict[str, tuple[str, datetime]] = {}
    for change in changes:
        if change.batch_id and change.batch_id not in batches:
            batches[change.batch_id] = (change.source, _utc(change.recorded_at))
    for summary in summaries:
        batches.setdefault(summary.batch_id, (summary.source, _utc(summary.recorded_at)))

    talk = [m for m in chat if m.id not in narrates]

    def reply_to_last_question(at: datetime) -> str | None:
        asked = None
        for message in talk:
            if message.role == "user" and _utc(message.created_at) <= at:
                asked = _utc(message.created_at)
        if asked is None:
            return None
        for message in talk:
            if message.role == "assistant" and _utc(message.created_at) >= asked:
                return message.id
        return None

    reply_batches: dict[str, list[str]] = {}
    for batch_id, (source, at) in batches.items():
        if source == CHAT_SOURCE:
            reply = reply_to_last_question(at)
            if reply:
                reply_batches.setdefault(reply, []).append(batch_id)

    summary_of = {s.batch_id: s.id for s in summaries}
    narrative_of = {batch_id: message_id for message_id, batch_id in narrates.items()}
    actions = [
        {
            "ref": m.id,
            "kind": "reply",
            "at": _utc(m.created_at).isoformat(),
            "batches": reply_batches.get(m.id, []),
        }
        for m in window
        if m.role == "assistant" and m.id not in narrates
    ] + [
        {
            "ref": batch_id,
            "kind": "plan_run",
            "at": at.isoformat(),
            "source": source,
            "summary": summary_of.get(batch_id),
            "message": narrative_of.get(batch_id),
        }
        for batch_id, (source, at) in batches.items()
        if source in COACH_RUNS
    ]
    actions.sort(key=lambda action: action["at"])
    return actions, narrates


async def build(
    user_email: str,
    until: date | None = None,
    days: int = 7,
    now: datetime | None = None,
) -> dict | None:
    """The digest of the account with this address, or None when there is none."""
    async with async_session_maker() as session:
        user = await session.scalar(
            select(models.User).where(func.lower(models.User.email) == user_email.lower())
        )
        if user is None:
            return None
        zone = _zone(user.timezone_name)
        if until is None:
            today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
            until = today - timedelta(days=1)
        start = until - timedelta(days=days - 1)
        begin = datetime.combine(start, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(until + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        first, after = start.isoformat(), (until + timedelta(days=1)).isoformat()
        ahead = (until + timedelta(days=AHEAD_DAYS)).isoformat()

        async def rows(model, *where, order_by=()):
            query = select(model).where(model.user_id == user.id, *where).order_by(*order_by)
            return list((await session.scalars(query)).all())

        history = models.PlanDayHistory
        # ponytail: the whole chat up to the window's end, so a flag can find an
        # older reply; one account, once a week.
        messages = await rows(
            models.ChatMessage,
            models.ChatMessage.created_at < end,
            order_by=(models.ChatMessage.created_at, models.ChatMessage.id),
        )
        changes = await rows(
            history,
            history.recorded_at >= begin,
            history.recorded_at < end,
            order_by=(history.recorded_at, history.id),
        )
        plan_rows = await rows(
            history,
            history.date >= first,
            history.date <= (until + timedelta(days=PLAN_DAYS_AFTER)).isoformat(),
            history.recorded_at < end,
            order_by=(history.recorded_at, history.id),
        )
        summaries = await rows(
            models.PlanChangeSummary,
            models.PlanChangeSummary.recorded_at >= begin,
            models.PlanChangeSummary.recorded_at < end,
            order_by=(models.PlanChangeSummary.recorded_at,),
        )
        rides = await rows(
            models.RideMetric,
            models.RideMetric.activity_date >= first,
            models.RideMetric.activity_date < after,
        )
        logs = await rows(
            models.WorkoutLog, models.WorkoutLog.date >= first, models.WorkoutLog.date < after
        )
        flags = await rows(
            models.WrongFlag,
            models.WrongFlag.created_at >= begin,
            models.WrongFlag.created_at < end,
            order_by=(models.WrongFlag.created_at,),
        )
        races = await rows(
            models.RaceEvent,
            models.RaceEvent.date >= first,
            models.RaceEvent.date <= ahead,
            order_by=(models.RaceEvent.date,),
        )
        goals = await rows(
            models.WeekGoal,
            # A week that starts up to six days before the window overlaps it.
            models.WeekGoal.week_start >= (start - timedelta(days=6)).isoformat(),
            models.WeekGoal.week_start <= ahead,
            order_by=(models.WeekGoal.week_start,),
        )
        athlete = {
            "timezone": user.timezone_name or "UTC",
            "current_ftp": user.current_ftp,
            "race_events": [
                {
                    "date": r.date,
                    "start_time": r.start_time,
                    "distance_km": r.distance_km,
                    "elevation_m": r.elevation_m,
                }
                for r in races
            ],
            "week_goals": [{"week_start": g.week_start, "goal": g.goal} for g in goals],
        }

    window = [m for m in messages if _utc(m.created_at) >= begin]
    context = [m for m in messages if _utc(m.created_at) < begin][-CONTEXT_MESSAGES:]
    actions, narrates = coach_actions(context + window, window, changes, summaries)
    events = (
        [_message(m, context=True) for m in context]
        + [_message(m, narrates.get(m.id)) for m in window]
        + [_change(c) for c in changes]
        + [_summary(s) for s in summaries]
        + [_ride(r, zone) for r in rides]
        + [_log(log, zone) for log in logs]
        + [_flag(f, messages) for f in flags]
    )
    # Every time is UTC in ISO form, so the text sorts in time order.
    events.sort(key=lambda event: (event["at"] or "", event["id"]))
    plan = [
        {"date": day.isoformat(), "slot": slot, "day": content}
        for (day, slot), content in sorted(final_plan(plan_rows).items())
    ]
    return {
        "format": FORMAT,
        "version": VERSION,
        "window": {"from": start.isoformat(), "until": until.isoformat()},
        "athlete": athlete,
        "plan": plan,
        "events": events,
        "coach_actions": actions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user-email", required=True, help="the one account to read")
    parser.add_argument(
        "--until",
        type=date.fromisoformat,
        default=None,
        help="last day of the window (default: yesterday in the account's time zone)",
    )
    parser.add_argument("--days", type=int, default=7, help="days in the window (default 7)")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")
    digest = asyncio.run(build(args.user_email, args.until, args.days))
    if digest is None:
        # Not the address itself: this lands in a workflow log.
        print("coach_week_digest: no account with that address", file=sys.stderr)
        return 2
    json.dump(digest, sys.stdout, ensure_ascii=False, default=str)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
