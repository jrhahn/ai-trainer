"""What the coach's reply changes, after the model has answered (ai-trainer-ops#247).

ask_trainer did it in some 220 lines after the model call: the app's own notes
under the reply, the ride note and label the coach corrected, the athlete's and
the coach's chat rows, the arrangement the coach committed to, and the plan
writes. They live here now, in the same order, with the helpers that write the
notes. Nothing here knows HTTP, and nothing here commits: the route does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from domain.availability import (
    SESSION_CAP,
    WEEKDAYS,
    weekday_label_for,
)
from services import chat_capture
from services import plan_commitments
from services import plan_pipeline
from services import reply_notes
from services.dates import app_today_iso
from services.plan_constraints import (
    day_session_cap,
    day_violates_constraint,
    describe_constraint_overrides,
    filter_plan_updates_for_constraints,
    has_session_cap,
    over_session_cap,
    session_cap_is_that_days_only,
    session_cap_minutes,
)

logger = logging.getLogger(__name__)


@dataclass
class ReplyOutcome:
    """What applying the reply produced, for the response."""

    notes: list[reply_notes.ReplyNote]
    updated_plan: list[dict] | None
    ride_label_updates: list[dict]
    # The coach's sentence per day the plan took (ai-trainer-ops#266).
    day_summaries: list[dict] = field(default_factory=list)


async def apply_reply(
    db: AsyncSession,
    current_user: models.User,
    result: dict,
    captured: chat_capture.Captured,
    *,
    question: str,
    already_in_chat: bool,
    notes_in_german: bool,
    timezone_name: str | None,
) -> ReplyOutcome:
    """What ask_trainer did with the model's reply before it committed, in the
    same order (ai-trainer-ops#247): the notes, the ride note and label, the
    chat rows, the plan commitment and the plan writes.

    ``result`` is the model's reply. Its ride note and label corrections are
    taken out of it; the rest is left for the response.
    """
    plan = captured.plan
    availability_constraints = captured.availability_constraints
    lifted_constraints = captured.lifted_constraints
    new_location = captured.new_location
    illness_restored = captured.illness_restored
    illness_easy_until = captured.illness_easy_until
    heart_rate_stripped = captured.heart_rate_stripped

    # The app's own notes under the reply, never in its text (ai-trainer-ops#262).
    notes: list[reply_notes.ReplyNote] = list(result.get("notes") or [])

    user_message_time = datetime.now(timezone.utc)
    assistant_message_time = user_message_time + timedelta(microseconds=1)
    requested_updates = result.get("plan_updates") or []
    plan_updates = _filter_plan_updates_for_availability_constraints(
        requested_updates,
        availability_constraints,
    )
    persisted_updated_plan: list[dict] | None = None

    # Coach honesty: ``commit_plan_updates`` only patches days already present in
    # the plan, so an update for a date the plan no longer contains (e.g. a past
    # day that rolled off the window) is a silent no-op. Don't let the model claim
    # success for a change that was never saved — append a correction to the reply
    # (also stored on the assistant message below) and drop the dead update. Only
    # do this when a plan exists; with no plan at all there is no window to reason
    # about. (#364)
    if plan_updates and plan:
        plan_dates = {d.get("date") for d in plan if isinstance(d, dict)}
        unresolved_dates = sorted(
            {u.get("date") for u in plan_updates if u.get("date")} - plan_dates
        )
        if unresolved_dates:
            joined = ", ".join(unresolved_dates)
            notes.append(
                reply_notes.note(
                    reply_notes.UNRESOLVED_DATES,
                    _unresolved_dates_note(joined, notes_in_german),
                )
            )
            plan_updates = [u for u in plan_updates if u.get("date") in plan_dates]

    # Coach honesty: hard availability constraints are enforced deterministically
    # by the plan pipeline, so a requested change to a constrained day never lands
    # as asked (a rest day on a required-session day is coerced back; training on
    # an unavailable day is dropped). Without a note the coach falsely confirms the
    # change (#414). Scope to days in the current plan window so dead dates stay
    # owned by the note above.
    flagged_constraint_dates: list[str] = []
    if plan and availability_constraints:
        plan_dates = {d.get("date") for d in plan if isinstance(d, dict)}
        overrides = describe_constraint_overrides(
            [u for u in requested_updates if u.get("date") in plan_dates],
            availability_constraints,
        )
        if overrides:
            notes.append(
                reply_notes.note(
                    reply_notes.CONSTRAINT_OVERRIDE,
                    _format_constraint_override_note(overrides, notes_in_german),
                )
            )
            # Remember which constraints blocked this turn so a follow-up "lift
            # that constraint" can resolve "that" to them (#437).
            flagged_constraint_dates = [o["date"] for o in overrides if o.get("date")]

    # A per-session limit holds for every stored session from now on, not only
    # for the days the coach changed: the write below shortens the others, even
    # when the coach changed nothing, and a note here says which (#185). The
    # note is written after the write, from what it shortened: the plan this
    # request read can be older than the one the write finds, when a rebuild
    # lands during the reply, and the note named the old plan's days
    # (ai-trainer-ops#332).
    session_cap_note_at = (
        len(notes) if plan and has_session_cap(availability_constraints) else None
    )

    # A day the athlete ruled out still holds a session the coach left in, or
    # changed without freeing it: the gate below frees it, and the note says so
    # (ai-trainer-ops#316). Twice an athlete said Thursday was out, and its
    # strength session stayed.
    if plan and availability_constraints:
        updates_by_date = {u.get("date"): u for u in plan_updates}
        ruled_out_dates = sorted(
            {
                str(d["date"])
                for d in plan
                if isinstance(d, dict)
                # A change the coach asked for there has its note above.
                and d.get("date") not in flagged_constraint_dates
                and day_violates_constraint(
                    {**d, **updates_by_date.get(d.get("date"), {})},
                    availability_constraints,
                )
            }
        )
        if ruled_out_dates:
            notes.append(
                reply_notes.note(
                    reply_notes.RULED_OUT_DAYS,
                    _ruled_out_note(ruled_out_dates, notes_in_german),
                )
            )

    # The illness's rest days came back before the coach answered; say which, in
    # fixed words (ai-trainer-ops#232).
    if illness_restored and illness_easy_until is not None:
        notes.append(
            reply_notes.note(
                reply_notes.ILLNESS_RETURN,
                _illness_return_note(
                    illness_restored, illness_easy_until, notes_in_german
                ),
            )
        )

    # Coach honesty: confirm a lift deterministically, independent of the model's
    # prose, so "pls lift that constraint" is not silently ignored (#437).
    if lifted_constraints:
        notes.append(
            reply_notes.note(
                reply_notes.CONSTRAINT_LIFT,
                _format_constraint_lift_note(lifted_constraints, notes_in_german),
            )
        )

    # Coach honesty: the training location was moved by the capture, before the model
    # replied, so confirm it deterministically rather than hoping the prose does (#495).
    if new_location:
        notes.append(
            reply_notes.note(
                reply_notes.LOCATION, _location_note(new_location, notes_in_german)
            )
        )

    # --- Phase 7: Persist inferred user ride feedback ---
    ride_note_update = result.pop("ride_note_update", None)
    if ride_note_update and isinstance(ride_note_update, dict):
        note_date = ride_note_update.get("activity_date")
        note_text = ride_note_update.get("note")
        if note_date and note_text:
            target_metrics = await crud.get_ride_metrics_by_date(
                db, current_user.id, note_date
            )
            if len(target_metrics) == 1:
                target_metric = target_metrics[0]
                await crud.update_ride_metric_notes(
                    db,
                    current_user.id,
                    target_metric.strava_activity_id,
                    user_note=note_text,
                )

    # --- Phase 7b: Persist AI-issued label corrections ---
    ride_label_update = result.pop("ride_label_update", None)
    ride_label_updates: list[dict] = []
    if ride_label_update and isinstance(ride_label_update, dict):
        label_date = ride_label_update.get("activity_date")
        label_text = ride_label_update.get("label")
        if label_date and label_text:
            target_metrics = await crud.get_ride_metrics_by_date(
                db, current_user.id, label_date
            )
            if len(target_metrics) == 1:
                await crud.update_ride_metric_notes(
                    db,
                    current_user.id,
                    target_metrics[0].strava_activity_id,
                    label_override=label_text,
                )
                ride_label_updates = [
                    {
                        "stravaActivityId": target_metrics[0].strava_activity_id,
                        "labelOverride": label_text,
                    }
                ]

    # Persist user and assistant chat messages
    if not already_in_chat:
        await crud.create_chat_message(
            db,
            current_user.id,
            role="user",
            content=question,
            timestamp=user_message_time.isoformat(),
        )
    assistant_message = await crud.create_chat_message(
        db,
        current_user.id,
        role="assistant",
        content=result["response"],
        timestamp=assistant_message_time.isoformat(),
        plan_update_count=len(plan_updates) if plan_updates else None,
        flagged_constraint_dates=flagged_constraint_dates or None,
        notes=notes or None,
    )

    # Record the arrangement *before* the plan write, so the guard protecting a
    # committed day is already in force for the very write that agreed it — a
    # coach that reschedules Thursday and commits to it in one turn must not have
    # its own Thursday edit blocked, and commit_plan_updates does not respect
    # pins, so it goes through (#667).
    await _record_plan_commitment(
        db, current_user, result.get("planCommitment"), timezone_name
    )

    # Apply plan updates if any — through the shared constraint-respecting pipeline.
    changed_dates: set[str] = set()
    applied_changes: list[dict] = []
    if plan_updates:
        commit = await plan_pipeline.commit_plan_updates(
            db,
            current_user,
            plan_updates,
            base_plan=plan,
            source="coach_chat",
            timezone_name=timezone_name,
        )
        persisted_updated_plan = commit.plan
        applied_changes.extend(commit.applied_changes)
        changed_dates = {
            str(change["date"])
            for change in commit.applied_changes
            if change.get("applied")
        }
    # The coach's sentence per changed day goes under the reply, for the days the
    # plan took: a day a guard kept as it was says nothing (ai-trainer-ops#266).
    day_summaries = [
        item
        for item in result.get("day_summaries") or []
        if item.get("date") in changed_dates
    ]
    assistant_message.day_summaries_json = day_summaries or None
    # The coach changed nothing, or changed nothing that went through, yet the
    # stored plan breaks the athlete's limit: the same gate shortens it (#185).
    # Keyed on the stored plan alone, so a rejected coach edit of an over-limit
    # day is shortened too (review 4 of #1000). A day the athlete ruled out is
    # freed the same way (ai-trainer-ops#316).
    if persisted_updated_plan is not None:
        stored = persisted_updated_plan
    elif plan and availability_constraints:
        # Read again, locked as the write reads it: a rebuild that landed during
        # the reply is the plan the gate must hold to the limit. Run on the plan
        # this request read, the gate kept the rebuild's longer days, since the
        # write keeps what changed since the read (ai-trainer-ops#332).
        row = await crud.get_training_plan_for_update(db, current_user.id)
        stored = row.plan if row is not None else plan
    else:
        stored = plan
    if any(
        isinstance(d, dict)
        and (
            over_session_cap(d, availability_constraints)
            or day_violates_constraint(d, availability_constraints)
        )
        for d in stored
    ):
        gated = await plan_pipeline.commit_plan(
            db,
            current_user,
            stored,
            base_plan=stored,
            source="coach_chat",
            timezone_name=timezone_name,
        )
        persisted_updated_plan = gated.plan
        applied_changes.extend(gated.applied_changes)

    # The sessions the writes shortened to the limit, besides the coach's own
    # days: the gate's own test on the day before and after, today included
    # (review 3 of #1000), so the note names exactly what the athlete will find.
    if session_cap_note_at is not None:
        updated_dates = {u.get("date") for u in requested_updates}
        capped_dates = sorted(
            {
                str(change["date"])
                for change in applied_changes
                if change.get("applied")
                and change.get("date") not in updated_dates
                and over_session_cap(
                    change.get("old_day") or {}, availability_constraints
                )
                and not over_session_cap(
                    change.get("new_day") or {}, availability_constraints
                )
            }
        )
        if capped_dates:
            notes.insert(
                session_cap_note_at,
                reply_notes.note(
                    reply_notes.SESSION_CAP,
                    _format_session_cap_note(
                        capped_dates, availability_constraints, notes_in_german
                    ),
                ),
            )
            assistant_message.notes_json = list(notes)

    # The capture's heart-rate caution stripped the stored plan before the coach
    # saw it, and neither the coach nor the cap wrote it again: the reply carries
    # it. Without it the screen kept "Puls unter 130 bpm" under a reply that had
    # just set heart rate aside, until the next load (ai-trainer-ops#226).
    if persisted_updated_plan is None and (heart_rate_stripped or illness_restored):
        persisted_updated_plan = plan

    return ReplyOutcome(
        notes=notes,
        updated_plan=persisted_updated_plan,
        ride_label_updates=ride_label_updates,
        day_summaries=day_summaries,
    )


# The app's own notes under a coach reply, in the athlete's language: an
# English "(Note: …)" under a German reply read as a system message, not as
# the coach (ai-trainer-ops#74 follow-up). Each returns the note's sentence;
# the reply carries it as a note beside its text (ai-trainer-ops#262).
_GERMAN_WEEKDAYS = {
    "monday": "Montag",
    "tuesday": "Dienstag",
    "wednesday": "Mittwoch",
    "thursday": "Donnerstag",
    "friday": "Freitag",
    "saturday": "Samstag",
    "sunday": "Sonntag",
}


def _location_note(where: str, german: bool) -> str:
    if german:
        return (
            f"Ich habe deinen üblichen Trainingsort auf {where} gesetzt. "
            f"Wetter und wetterbedingte Planänderungen richten sich jetzt danach."
        )
    return (
        f"I've set your usual training location to {where}, so your weather "
        f"outlook and any weather-driven plan changes now use that."
    )


def _unresolved_dates_note(joined: str, german: bool) -> str:
    if german:
        return (
            f"{joined} konnte ich nicht ändern — der Tag gehört nicht "
            f"zu deinem aktuellen Trainingsplan, es wurde nichts gespeichert."
        )
    return (
        f"I could not update {joined} — that day is not part of your "
        f"current training plan, so no change was saved."
    )


def _illness_return_note(
    restored: dict[tuple[str, int], dict], easy_until: _date, german: bool
) -> str:
    """Name the sessions an ended illness gave back, and which of them are easy
    (ai-trainer-ops#232). Fixed words: the router wrote them, not the coach."""
    days = []
    for iso in sorted({key[0] for key in restored}):
        label = (
            _day_label({"date": iso, "weekday": weekday_label_for(iso)}, german)
            + f" {iso}"
        )
        if _date.fromisoformat(iso) <= easy_until:
            label += " (locker)" if german else " (easy)"
        days.append(label)
    if german:
        return f"Diese Einheiten sind wieder in deinem Plan: {', '.join(days)}."
    return f"These sessions are back in your plan: {', '.join(days)}."


def _filter_plan_updates_for_availability_constraints(
    updates: list[dict], constraints: list[dict]
) -> list[dict]:
    return filter_plan_updates_for_constraints(updates, constraints)


def _day_label(override: dict, german: bool = False) -> str:
    """Human-friendly day label for a constraint override note."""
    weekday = override.get("weekday")
    if weekday:
        if german:
            return _GERMAN_WEEKDAYS.get(str(weekday).lower(), str(weekday).capitalize())
        return str(weekday).capitalize()
    return str(override.get("date") or ("diesen Tag" if german else "that day"))


def _format_constraint_override_note(
    overrides: list[dict], german: bool = False
) -> str:
    """Compose an honest note about coach-requested changes a constraint blocked.

    The plan pipeline enforces hard availability constraints deterministically,
    so these requests never land as asked. Without this, the coach would falsely
    confirm the change (#414).
    """
    required = [o for o in overrides if o.get("constraintType") == "required_workout"]
    unavailable = [o for o in overrides if o.get("constraintType") == "no_training"]
    capped = [o for o in overrides if o.get("constraintType") == SESSION_CAP]
    sentences: list[str] = []
    # One sentence per limit: a weekday may have its own (ai-trainer-ops#231).
    for cap in dict.fromkeys(o.get("capMinutes") for o in capped):
        days = ", ".join(
            _day_label(o, german) for o in capped if o.get("capMinutes") == cap
        )
        sentences.append(
            f"{days} habe ich auf {cap} min gekürzt: Das ist deine Grenze pro Einheit."
            if german
            else f"I kept {days} to {cap} min: that is your limit per session."
        )
    if required and german:
        days = ", ".join(_day_label(o, german) for o in required)
        sentences.append(
            f"{days} konnte ich nicht ändern: Eine deiner Verfügbarkeitsvorgaben legt "
            f"dort eine Pflichteinheit fest, also bleibt der Plan so. Sag Bescheid, "
            f"wenn ich die Vorgabe aufheben soll."
        )
    elif required:
        days = ", ".join(_day_label(o) for o in required)
        sentences.append(
            f"I couldn't change {days}: it's pinned as a required session by one of "
            f"your availability constraints, so the plan keeps it as-is. Let me know "
            f"if you'd like to lift that constraint."
        )
    if unavailable and german:
        days = ", ".join(_day_label(o, german) for o in unavailable)
        sentences.append(
            f"Für {days} konnte ich kein Training einplanen: Eine deiner "
            f"Verfügbarkeitsvorgaben markiert den Tag als nicht verfügbar."
        )
    elif unavailable:
        days = ", ".join(_day_label(o) for o in unavailable)
        sentences.append(
            f"I couldn't schedule training on {days}: it's marked unavailable by one "
            f"of your availability constraints."
        )
    return " ".join(sentences)


def _ruled_out_note(dates: list[str], german: bool = False) -> str:
    """Say which ruled-out days the app freed (ai-trainer-ops#316)."""
    if german:
        named = " und ".join(
            f"am {_day_label({'weekday': weekday_label_for(d)}, True)}, {d[8:10]}.{d[5:7]}."
            for d in dates
        )
        sessions = "die Einheit" if len(dates) == 1 else "die Einheiten"
        return f"Du hast gesagt, dass du {named} nicht kannst, deshalb habe ich {sessions} gestrichen."
    named = " and ".join(
        f"{(weekday_label_for(d) or '').capitalize()} {d}" for d in dates
    )
    if len(dates) == 1:
        return f"You said {named} does not work, so I took that session out."
    return f"You said {named} do not work, so I took those sessions out."


def _format_session_cap_note(
    dates: list[str], constraints: list[dict], german: bool = False
) -> str:
    """Say which stored sessions a per-session limit shortened (#185), one
    sentence per limit: a weekday may have its own (ai-trainer-ops#231), and a
    limit said for one day says so, so the athlete can correct it (#295)."""
    every = session_cap_minutes(constraints)
    groups: dict[tuple[int | None, bool], list[str]] = {}
    for d in dates:
        once = session_cap_is_that_days_only({"date": d}, constraints)
        groups.setdefault((day_session_cap({"date": d}, constraints), once), []).append(
            d
        )
    sentences = []
    for (cap, once), group in groups.items():
        days = ", ".join(
            _day_label({"date": d, "weekday": weekday_label_for(d)}, german) + f" {d}"
            for d in group
        )
        if once:
            if german:
                named = " und ".join(
                    f"am {_day_label({'weekday': weekday_label_for(d)}, True)}, {d[8:10]}.{d[5:7]}."
                    for d in group
                )
                sessions = "die Einheit" if len(group) == 1 else "die Einheiten"
                sentences.append(
                    f"Nur {named} hast du höchstens {cap} min, deshalb habe ich {sessions} gekürzt."
                )
            else:
                named = " and ".join(
                    f"{(weekday_label_for(d) or '').capitalize()} {d}" for d in group
                )
                sessions = "that session" if len(group) == 1 else "those sessions"
                sentences.append(
                    f"Only on {named} you have at most {cap} min, so I shortened {sessions}."
                )
            continue
        if cap == every:
            sentences.append(
                f"Deine Grenze von {cap} min pro Einheit gilt jetzt für jede Einheit, "
                f"deshalb habe ich {days} gekürzt."
                if german
                else f"Your limit of {cap} min per session now applies to every session, "
                f"so I shortened {days}."
            )
            continue
        # In week order, as the athlete says them, not in the order the plan
        # reaches them: on a Tuesday the next Thursday comes first, and the
        # note said "Donnerstags und dienstags".
        weekdays = sorted(
            {weekday_label_for(d) or "" for d in group},
            key=lambda w: WEEKDAYS.get(w, len(WEEKDAYS)),
        )
        if german:
            named = " und ".join(
                _GERMAN_WEEKDAYS.get(w, w).lower() + "s" for w in weekdays
            )
            sentences.append(
                f"{named[:1].upper()}{named[1:]} hast du höchstens {cap} min, "
                f"deshalb habe ich {days} gekürzt."
            )
        else:
            named = " and ".join(f"{w.capitalize()}s" for w in weekdays)
            sentences.append(
                f"On {named} you have at most {cap} min, so I shortened {days}."
            )
    return " ".join(sentences)


def _format_constraint_lift_note(lifted: list[dict], german: bool = False) -> str:
    """Confirm, deterministically, which availability constraints were lifted.

    The lift is applied by the router before the plan pipeline runs, so this is
    the honest record of what happened — independent of whatever the model says
    in its prose (#437).
    """
    days = ", ".join(_day_label(c, german) for c in lifted)
    if german:
        return (
            f"Ich habe die Verfügbarkeitsvorgabe für {days} aufgehoben, "
            f"der Plan lässt sich dort jetzt ändern."
        )
    plural = "constraints" if len(lifted) > 1 else "constraint"
    return (
        f"I lifted the availability {plural} on {days}, so the plan can now "
        f"be changed there."
    )


async def _record_plan_commitment(
    db: AsyncSession,
    user: models.User,
    proposed: object,
    timezone_name: str | None,
) -> None:
    """Persist the arrangement the coach attached to a plan change (#667).

    Fail-safe by construction: an unusable window or a missing sentence is
    dropped and a storage failure is swallowed. A commitment makes later writes
    *safer*, so failing to record one must never cost the athlete the plan change
    it accompanied — that would trade a small loss for a large one.
    """
    if not isinstance(proposed, dict):
        return
    text = str(proposed.get("text") or "").strip()
    if not text:
        return
    window = plan_commitments.normalize_window(
        proposed.get("startDate"), proposed.get("endDate")
    )
    if window is None:
        return
    try:
        async with db.begin_nested():
            today = app_today_iso(timezone_name=timezone_name)
            await crud.deactivate_expired_plan_commitments(db, user.id, today=today)
            await crud.create_plan_commitment(
                db,
                user.id,
                start_date=window[0],
                end_date=window[1],
                text=text,
                source="coach_chat",
            )
    except Exception:  # noqa: BLE001 — never let this cost the plan change
        logger.warning("could not record plan commitment", exc_info=True)
