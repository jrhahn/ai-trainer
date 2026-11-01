"""What the athlete's chat message says, read before the coach is asked (ai-trainer-ops#247).

ask_trainer read it in seven helpers and some sixty lines of its own: the days
the athlete cannot train, a location or a goal stated in passing, an illness
and its end, heart-rate medication, a race, the profile changes it offers
(hours, days, an FTP) and a preferred weekday. They live here now, moved as
they were, and ``capture_from_message`` runs them in the same order. Nothing
here knows HTTP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from domain import day_preference
from services import motivation_inference
from services import (
    goal_progress,
    illness_return,
    profile_intent,
    race_intent,
    stated_ftp,
)
from domain import athlete_states
from domain import red_flags
from services import heart_rate_caution, plan_context
from services import plan_pipeline
from services.dates import app_today, app_today_iso
from domain.availability import (
    constraint_label,
    extract_availability_constraints,
    extract_constraint_lift,
)
from services import home_location, weather_preference
from services.weather_service import clear_forecast_cache

logger = logging.getLogger(__name__)

# As in routers/ai.py: the chat reads the athlete's active constraints the way
# the planner does, and puts them into the profile the same way.
_active_availability_constraints_for_prompt = (
    plan_context.active_availability_constraints
)
_profile_with_availability_constraints = (
    plan_context.profile_with_availability_constraints
)


@dataclass
class Captured:
    """What the athlete's message says, read before the model is asked.

    ``plan`` and ``profile`` are the ones the rest of the turn works with: an
    illness that ended gives its rest days back, heart-rate medication strips
    heart-rate targets, and a kept day preference and the availability go
    into the profile the prompts read.
    """

    plan: list[dict]
    profile: dict
    availability_constraints: list[dict]
    lifted_constraints: list[dict]
    captured_constraints: list[dict]
    new_location: str | None
    captured_goals: list[models.AthleteGoal]
    captured_states: list[dict]
    illness_restored: dict[tuple[str, int], dict]
    illness_easy_until: _date | None
    heart_rate_stripped: bool
    # The warning sign the message states, if any (ai-trainer-ops#338).
    red_flag: str | None
    # The message said, for the first time, that a medication blunts the
    # heart rate: the reply then names a doctor (ai-trainer-ops#338).
    heart_rate_newly_off: bool
    race_proposal: dict | None
    race_offer: dict | None
    profile_proposal: dict | None
    captured_day_preferences: list[dict]


async def capture_from_message(
    db: AsyncSession,
    current_user: models.User,
    question: str,
    *,
    plan: list[dict],
    profile: dict,
    timezone_name: str | None,
) -> Captured:
    """Everything the chat reads from the athlete's own words before it asks the
    model, in the order ask_trainer always read it (ai-trainer-ops#247)."""
    (
        availability_constraints,
        lifted_constraints,
        captured_constraints,
    ) = await _capture_availability_constraints_from_message(
        db, current_user.id, question, timezone_name
    )
    # Capture weather self-reports before the forecast is read, so a location the
    # athlete states in this very message anchors this turn's outlook (#495).
    new_location, captured_goals = await _capture_self_reported_signals_from_message(
        db, current_user.id, question
    )
    captured_states, ended_illness = await _capture_athlete_states(
        db, current_user.id, question, timezone_name
    )
    plan, illness_restored, illness_easy_until = await _restore_illness_days(
        db, current_user, plan, ended_illness, timezone_name
    )
    heart_rate_was_off = bool(current_user.heart_rate_targets_off)
    heart_rate_chips, plan, heart_rate_stripped = await _capture_heart_rate_caution(
        db, current_user, plan, question, timezone_name
    )
    captured_states = [*captured_states, *heart_rate_chips]
    # No max HR to build zones from while a medication blunts it (#184).
    profile = heart_rate_caution.prompt_profile(
        profile, current_user.heart_rate_targets_off
    )
    # Offered, never applied: the athlete confirms in the chat (ops#71, #63 Q21).
    race_proposal = await _race_proposal(db, current_user, question, timezone_name)
    # A race said to be coming is offered for the calendar (ai-trainer-ops#175);
    # never in the same reply that offers to clear the races.
    race_offer = (
        None
        if race_proposal
        else await _race_offer(db, current_user, question, timezone_name)
    )
    # Hours, days, sports, level or max heart rate said in chat: offered, never
    # applied (ops#43, #39, #202).
    profile_proposal = profile_intent.propose_profile_change(
        question,
        weekly_hours=current_user.weekly_hours,
        training_days=current_user.training_days,
        sports=current_user.sports,
        fitness_level=current_user.fitness_level,
        max_heart_rate=current_user.max_heart_rate,
        resting_heart_rate=current_user.resting_heart_rate,
    )
    # An FTP said in chat is offered too, not set (ai-trainer-ops#129, the owner's
    # choice of 2026-10-18). It moves every power target in the plan, so a
    # sentence read wrongly must not change them without a tap; it was set at
    # once with a note before (#70).
    stated_ftp_watts = stated_ftp.extract_stated_ftp(question)
    if stated_ftp_watts is not None and stated_ftp_watts != current_user.current_ftp:
        profile_proposal = {**(profile_proposal or {}), "ftp": stated_ftp_watts}
    # A weekday preferred over another (ai-trainer-ops#204). Kept when the plan
    # can act on it, both days being training days; offered as a swap of the
    # training days when it is general ("generally", "meistens") and the
    # preferred day is not one. Shown under the reply either way.
    profile, profile_proposal, captured_day_preferences = _capture_day_preferences(
        current_user, question, profile, profile_proposal
    )
    profile = _profile_with_availability_constraints(profile, availability_constraints)
    return Captured(
        plan=plan,
        profile=profile,
        availability_constraints=availability_constraints,
        lifted_constraints=lifted_constraints,
        captured_constraints=captured_constraints,
        new_location=new_location,
        captured_goals=captured_goals,
        captured_states=captured_states,
        illness_restored=illness_restored,
        illness_easy_until=illness_easy_until,
        heart_rate_stripped=heart_rate_stripped,
        red_flag=red_flags.red_flag(question),
        heart_rate_newly_off=(
            not heart_rate_was_off and bool(current_user.heart_rate_targets_off)
        ),
        race_proposal=race_proposal,
        race_offer=race_offer,
        profile_proposal=profile_proposal,
        captured_day_preferences=captured_day_preferences,
    )


async def _capture_availability_constraints_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
    timezone_name: str | None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Sync availability constraints with the athlete's message.

    Lifts requested constraints first so a same-message re-proposed edit sees the
    post-lift state, then captures any newly stated constraints. Returns the
    active constraints for the prompt, the constraints that were lifted (#437),
    and the ones this message stated, as chips to keep or remove
    (ai-trainer-ops#110).
    """
    today = app_today(timezone_name=timezone_name)

    # Lift before capture: honour "lift that constraint" so the coach's edit to a
    # previously blocked day can finally land in the same turn (#437).
    lift = extract_constraint_lift(message, today=today)
    lifted_rows: list[models.AthleteAvailabilityConstraint] = []
    if lift["lift"]:
        dates = lift["dates"] or await crud.get_last_flagged_constraint_dates(
            db, user_id
        )
        if dates:
            lifted_rows = await crud.deactivate_availability_constraints_for_dates(
                db, user_id, dates
            )
    lifted = [
        schemas.AthleteAvailabilityConstraintSchema.model_validate(
            row, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for row in lifted_rows
    ]

    extracted = extract_availability_constraints(message, today=today)
    captured = []
    for item in extracted:
        row = await crud.upsert_availability_constraint(db, user_id, **item)
        captured.append(
            {
                "id": row.id,
                "label": constraint_label(
                    row.constraint_type,
                    row.constraint_date,
                    row.required_workout,
                    row.weekday,
                ),
                "removable": True,
            }
        )
    constraints = await _active_availability_constraints_for_prompt(
        db, user_id, timezone_name
    )
    return constraints, lifted, captured


async def _capture_self_reported_signals_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
) -> tuple[str | None, list[models.AthleteGoal]]:
    """Sync the athlete's self-reports from their message (#495, #563).

    Three independent captures, all deterministic and all best-effort:

    * a stated training location ("I mostly train near Freiburg now") becomes the
      ``user_set`` home-location attribute, which then outranks every inference
      pass and re-anchors all weather lookups;
    * a stated weather preference ("I actually love the rain") becomes evidence on
      the corresponding weather-preference belief, reinforcing the same row the
      ride data writes;
    * a stated motivation ("I don't care about races", "I want more trail time")
      becomes evidence on the athlete's motivation model — what the training is
      actually for.

    Returns the new training location's label when it moved, for the
    deterministic confirmation note — the honest record of what the backend
    actually did, independent of the model's prose (the same contract as the
    availability-constraint lift note, #437).
    """
    try:
        async with db.begin_nested():
            await weather_preference.capture_weather_preferences_from_message(
                db, user_id, message
            )
    except Exception:
        logger.warning("Weather-preference capture failed", exc_info=True)

    # What the athlete says they are training for (#563). Same contract as the
    # captures around it: deterministic, best-effort, and unable to overwrite an
    # objective the athlete set by hand.
    try:
        async with db.begin_nested():
            await motivation_inference.capture_motivation_from_message(
                db, user_id, message
            )
    except Exception:
        logger.warning("Motivation capture failed", exc_info=True)

    # A goal with a number on it ("4000 hm in one ride"), so the coach can say
    # how far away it is rather than only repeat it (ai-trainer-ops#65).
    # One savepoint per goal: a write that fails (say, the one-active-goal index
    # losing a race with a second message) costs that goal only, never the
    # captures after it on the same session.
    captured_goals: list[models.AthleteGoal] = []
    for stated in goal_progress.extract_goals(message):
        try:
            async with db.begin_nested():
                goal = await crud.upsert_active_goal(
                    db,
                    user_id,
                    metric=stated.metric,
                    scope=stated.scope,
                    target=stated.target,
                    statement=stated.statement,
                )
                captured_goals.append(goal)
        except Exception:
            logger.warning("Goal capture failed", exc_info=True)

    try:
        async with db.begin_nested():
            row = await home_location.capture_home_location_from_message(
                db, user_id, message
            )
    except Exception:
        logger.warning("Home-location capture failed", exc_info=True)
        return None, captured_goals
    if row is None:
        return None, captured_goals

    # A new base invalidates the cached forecast for the old one.
    clear_forecast_cache()
    return row.label or f"{row.latitude:.2f}, {row.longitude:.2f}", captured_goals


async def _capture_athlete_states(
    db: AsyncSession, user_id: str, message: str, timezone_name: str | None
) -> tuple[list[dict], models.AthleteState | None]:
    """Store what the athlete says about being ill or tired, for the reply to show (#18).

    Before the prompt is built, so this turn's plan already sees it. Kept as a
    prior, never a guard (#39 Q1): ``plan_priors`` reports, the coach decides.
    Best-effort like the captures above: a failed write costs the state only.
    Returns the chips, and the illness this message ended, if any: its rest
    days come back (ai-trainer-ops#232).
    """
    today = app_today(timezone_name=timezone_name)
    captured: list[dict] = []
    ended = None
    try:
        async with db.begin_nested():
            # "Ich war nicht krank" takes the illness back: deleted, with no easy
            # return days, unlike "wieder gesund" (ai-trainer-ops#115).
            if athlete_states.says_not_ill(message):
                retracted = await crud.retract_illness(db, user_id, today)
                if retracted is not None:
                    captured.append(
                        {
                            "id": retracted,
                            "label": athlete_states.retracted_label(),
                            "removable": False,
                        }
                    )
            elif athlete_states.says_recovered(message):
                ended = await crud.end_illness(db, user_id, today)
                if ended is not None and ended.ends_on:
                    captured.append(
                        {
                            "id": ended.id,
                            "label": athlete_states.recovered_label(
                                _date.fromisoformat(ended.ends_on),
                                _return_days(ended),
                            ),
                            "removable": False,
                        }
                    )
            flagged = red_flags.red_flag(message)
            if not flagged and athlete_states.says_cleared(message):
                cleared = await crud.end_clearance(db, user_id, today)
                if cleared is not None:
                    captured.append(
                        {
                            "id": cleared.id,
                            "label": athlete_states.cleared_label(),
                            "removable": False,
                        }
                    )
            stated_states = athlete_states.extract_states(message, today)
            # A warning sign (chest pain, fainting, …) holds every session until
            # the athlete says a doctor has checked it (review of 2026-11-01,
            # ai-trainer-ops#338, A1).
            if flagged:
                stated_states.insert(
                    0,
                    athlete_states.StatedState(
                        athlete_states.KIND_CLEARANCE, today, None
                    ),
                )
            for stated in stated_states:
                row = await crud.record_state(db, user_id, stated, today)
                # "Ich war krank, seit gestern fieberfrei" both ends the stored
                # illness and states it ended: one row, so one chip.
                if any(chip["id"] == row.id for chip in captured):
                    continue
                captured.append(
                    {
                        "id": row.id,
                        "label": athlete_states.label(
                            row.kind,
                            _date.fromisoformat(row.starts_on),
                            _date.fromisoformat(row.ends_on) if row.ends_on else None,
                        ),
                        "removable": True,
                    }
                )
    except Exception:
        logger.warning("Athlete-state capture failed", exc_info=True)
        return [], None
    return captured, ended


def _return_days(ended: models.AthleteState) -> int:
    """The easy days after the illness that just ended."""
    return athlete_states.return_days(
        ended.kind,
        _date.fromisoformat(ended.starts_on),
        _date.fromisoformat(ended.ends_on),
    )


async def _restore_illness_days(
    db: AsyncSession,
    user: models.User,
    plan: list[dict],
    ended: models.AthleteState | None,
    timezone_name: str | None,
) -> tuple[list[dict], dict[tuple[str, int], dict], _date | None]:
    """Give back the sessions the illness that just ended turned into rest
    (ai-trainer-ops#232, the owner's (a)): from the plan history, easy inside the
    easy days. Before the prompt is built, so the coach sees the plan they are in.
    Returns the plan as it now stands, the sessions that came back, and the last
    easy day. Best-effort: a failed write costs the restore, not the reply.
    """
    if ended is None or not ended.ends_on or not plan:
        return plan, {}, None
    today = app_today(timezone_name=timezone_name)
    easy_until = _date.fromisoformat(ended.ends_on) + timedelta(
        days=_return_days(ended)
    )
    try:
        async with db.begin_nested():
            restored = await illness_return.restored_sessions(
                db, user, ended, plan, today=today, easy_until=easy_until
            )
            if not restored:
                return plan, {}, None
            commit = await plan_pipeline.commit_plan(
                db,
                user,
                [restored.get(schemas.session_key(day), day) for day in plan],
                base_plan=plan,
                source="illness_return",
                timezone_name=timezone_name,
            )
        return commit.plan, restored, easy_until
    except Exception:
        logger.warning("The illness's rest days could not be restored", exc_info=True)
        return plan, {}, None


# The chip under the reply, and the id its Remove sends (ai-trainer-ops#184).
HEART_RATE_CAUTION_ID = "heart-rate-caution"


HEART_RATE_OFF_LABEL = (
    "Heart-rate targets are off: your plan steers by effort and power"
)


HEART_RATE_BACK_LABEL = "Heart-rate targets can come back in new sessions"


async def _capture_heart_rate_caution(
    db: AsyncSession,
    user: models.User,
    plan: list[dict],
    message: str,
    timezone_name: str | None,
) -> tuple[list[dict], list[dict], bool]:
    """A medication that blunts the heart rate, said in chat (ai-trainer-ops#184).

    Applied at once, with a chip that says so and can take it back: a bpm target
    the athlete can no longer reach is a safety point, not a matter of taste,
    and stripping it costs no model call. Before the prompt is built, so this
    turn's coach already sees the plan without them. Returns the chips, the
    plan as it now stands, and whether this stripped it: the reply then carries
    that plan (ai-trainer-ops#226).
    """
    try:
        if heart_rate_caution.says_stopped(message) and user.heart_rate_targets_off:
            user.heart_rate_targets_off = False
            await db.flush()
            return (
                [
                    {
                        "id": HEART_RATE_CAUTION_ID,
                        "label": HEART_RATE_BACK_LABEL,
                        "removable": False,
                    }
                ],
                plan,
                False,
            )
        if not heart_rate_caution.says_blunted(message):
            return [], plan, False
        user.heart_rate_targets_off = True
        await db.flush()
        stripped = False
        if plan and any(heart_rate_caution.sets_heart_rate_target(d) for d in plan):
            # A failed strip must not cost the flag or the session: the gate
            # still keeps every later write free of targets (review of #998).
            try:
                async with db.begin_nested():
                    commit = await plan_pipeline.commit_plan(
                        db,
                        user,
                        plan,
                        base_plan=plan,
                        source="heart_rate_caution",
                        timezone_name=timezone_name,
                    )
                plan = commit.plan
                stripped = True
            except Exception:
                logger.warning(
                    "Heart-rate targets could not be stripped now", exc_info=True
                )
        return (
            [
                {
                    "id": HEART_RATE_CAUTION_ID,
                    "label": HEART_RATE_OFF_LABEL,
                    "removable": True,
                }
            ],
            plan,
            stripped,
        )
    except Exception:
        logger.warning("Heart-rate caution capture failed", exc_info=True)
        return [], plan, False


async def _race_proposal(
    db: AsyncSession, user: models.User, message: str, timezone_name: str | None
) -> dict | None:
    """The offer to clear the race the athlete says is off, or ``None``.

    The button clears the profile's race and every upcoming calendar race, so it
    is offered only when all of them fall in the period the athlete named: "kein
    Rennen dieses Jahr, aber nächstes" must not delete next year's races
    (ai-trainer-ops#129).
    """
    if not (user.race_date or user.training_goal == "race"):
        return None
    today = app_today_iso(timezone_name=timezone_name)
    scope = race_intent.no_race_scope(message, _date.fromisoformat(today))
    if scope is None:
        return None
    upcoming = [e for e in await crud.get_race_events(db, user.id) if e.date >= today]
    races = [_date.fromisoformat(e.date) for e in upcoming]
    # A race already run is history, not a race the offer would clear
    # (review of #934).
    if user.race_date and str(user.race_date)[:10] >= today:
        races.append(_date.fromisoformat(str(user.race_date)[:10]))
    if not all(scope.covers(race) for race in races):
        return None
    return {
        "race_date": user.race_date,
        "race_description": user.race_description,
        # Clearing also removes upcoming calendar races; the button says so.
        "calendar_races": len(upcoming),
    }


async def _race_offer(
    db: AsyncSession, user: models.User, message: str, timezone_name: str | None
) -> dict | None:
    """A race the athlete says is coming, offered for the calendar (ai-trainer-ops#175).

    Only offered, never saved: the date is resolved here ("in 3 weeks") and
    shown, so the athlete can correct it. A race already on that date, in the
    calendar or on the profile, is not offered again.
    """
    today = app_today(timezone_name=timezone_name)
    day = race_intent.stated_race_date(message, today)
    if day is None:
        return None
    iso = day.isoformat()
    if user.race_date and str(user.race_date)[:10] == iso:
        return None
    events = await crud.get_race_events(db, user.id)
    if any(event.date == iso for event in events):
        return None
    offer: dict = {"date": iso}
    if race_intent.stated_race_move(message, today):
        moved = _race_being_moved(user, events, today.isoformat())
        if moved:
            offer["replaces"] = moved
    return offer


def _race_being_moved(user: models.User, events: list, today: str) -> dict | None:
    """The race "the race is moved" means: the profile's when it is ahead, else
    the one race ahead in the calendar. With several, nobody can tell which, so
    the offer adds one as before (#175, comment 5111)."""
    if user.race_date and str(user.race_date)[:10] >= today:
        return {"date": str(user.race_date)[:10], "kind": "profile"}
    ahead = [event for event in events if (event.date or "") >= today]
    if len(ahead) == 1:
        return {"date": ahead[0].date, "kind": "calendar", "event_id": ahead[0].id}
    return None


def _capture_day_preferences(
    user: models.User,
    message: str,
    profile: dict,
    profile_proposal: dict | None,
) -> tuple[dict, dict | None, list[dict]]:
    """Keep or offer the day preferences ``message`` states (ai-trainer-ops#204).

    Returns the profile the coach sees this turn, the profile proposal, and the
    preferences kept, as chips.
    """
    stated = day_preference.extract(message)
    kept = [p for p in stated if day_preference.honoured([p], user.training_days)]
    if kept:
        user.day_preferences = day_preference.merged(user.day_preferences, kept)
        profile = {
            **profile,
            "dayPreferences": [
                day_preference.label(p)
                for p in day_preference.honoured(
                    user.day_preferences, user.training_days
                )
            ],
        }
    if not (profile_proposal or {}).get("training_days"):
        for item in stated:
            swapped = day_preference.swapped_training_days(user.training_days, item)
            if swapped:
                profile_proposal = {
                    **(profile_proposal or {}),
                    "training_days": swapped,
                    "standing_day_swap": True,
                }
                break
    return (
        profile,
        profile_proposal,
        [{"prefer": p["prefer"], "over": p["over"]} for p in kept],
    )
