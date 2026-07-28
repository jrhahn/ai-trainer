"""Tests for the unified plan pipeline (services/plan_pipeline.py).

These lock in the two cross-cutting guarantees every plan-writing trigger now
inherits: hard availability constraints are enforced, and concurrent user edits
survive a write (the reload-revert, #339).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
import models
from auth import hash_password
from services import plan_pipeline, summary_pipeline
from services.dates import app_today
from tests.conftest import TestSessionLocal


def _day(date: str, workout_type: str = "endurance", duration: int = 60, completed: bool = False) -> dict:
    return {
        "date": date,
        "workoutType": workout_type,
        "title": f"Workout {date}",
        "description": "Session",
        "durationMinutes": duration,
        "completed": completed,
    }


async def _create_user(email: str, plan: list[dict]) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_commit_plan_enforces_no_training_constraint():
    """A proposed training day on a hard no_training date is forced to rest."""
    # Relative to "today" so the constraint's expiry (expires_on=d) stays in the
    # future — a hardcoded past date would silently expire and stop enforcing.
    d = (app_today() + timedelta(days=3)).isoformat()
    user_id = await _create_user("pipe-constraint@example.com", [_day(d, "intervals")])
    async with TestSessionLocal() as db:
        await crud.upsert_availability_constraint(
            db, user_id, constraint_type="no_training", constraint_date=d, expires_on=d
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan(
            db, user, [_day(d, "intervals")], base_plan=[_day(d, "intervals")],
            source="adapt",
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["workoutType"] == "rest"
    assert day["durationMinutes"] == 0


@pytest.mark.asyncio
async def test_commit_plan_preserves_concurrent_user_edit():
    """A day the user changed concurrently must not be overwritten (#339)."""
    d = "2026-07-11"
    base = [_day(d, "endurance")]
    user_id = await _create_user("pipe-merge@example.com", base)

    # User edits the day in the DB after the trigger captured `base`.
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [_day(d, "rest", duration=0)])
        await db.commit()

    # Trigger proposes a different value, still based on the stale `base`.
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan(
            db, user, [_day(d, "intervals")], base_plan=base, source="adapt"
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["workoutType"] == "rest"  # the user's concurrent edit wins


@pytest.mark.asyncio
async def test_commit_plan_updates_drops_constraint_violating_update():
    """commit_plan_updates must not apply an update that violates a constraint."""
    # Relative to "today" so the constraint's expiry (expires_on=d) stays in the
    # future — a hardcoded past date would silently expire and stop enforcing.
    d = (app_today() + timedelta(days=3)).isoformat()
    user_id = await _create_user("pipe-updates@example.com", [_day(d, "rest", duration=0)])
    async with TestSessionLocal() as db:
        await crud.upsert_availability_constraint(
            db, user_id, constraint_type="no_training", constraint_date=d, expires_on=d
        )
        await db.commit()

    base = [_day(d, "rest", duration=0)]
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "workoutType": "intervals", "durationMinutes": 90}],
            base_plan=base,
            source="coach_chat",
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["workoutType"] == "rest"


@pytest.mark.asyncio
async def test_commit_plan_enforces_required_workout():
    """A pinned required session is coerced onto a day that falls short."""
    # Relative to "today" so the constraint's expiry (expires_on=d) stays in the
    # future — a hardcoded past date would silently expire and stop enforcing.
    d = (app_today() + timedelta(days=3)).isoformat()
    user_id = await _create_user("pipe-required@example.com", [_day(d, "rest", duration=0)])
    async with TestSessionLocal() as db:
        await crud.upsert_availability_constraint(
            db,
            user_id,
            constraint_type="required_workout",
            constraint_date=d,
            expires_on=d,
            required_workout={"workoutType": "endurance", "minDurationMinutes": 120},
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan(
            db,
            user,
            [_day(d, "rest", duration=0)],
            base_plan=[_day(d, "rest", duration=0)],
            source="adapt",
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["workoutType"] == "endurance"
    assert day["durationMinutes"] == 120


@pytest.mark.asyncio
async def test_plan_change_invalidates_login_summary():
    """A real plan change clears the login summary so it regenerates on load."""
    d = "2026-07-20"
    user_id = await _create_user("pipe-summary@example.com", [_day(d, "endurance")])
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(
            db,
            user_id,
            estimated_ftp=250,
            rider_type="allrounder",
            login_summary="Old summary based on the previous plan.",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, [_day(d, "intervals")], base_plan=[_day(d, "endurance")],
            source="adapt",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.login_summary is None


@pytest.mark.asyncio
async def test_no_op_plan_write_keeps_login_summary():
    """An unchanged plan write must not invalidate the summary."""
    d = "2026-07-21"
    plan = [_day(d, "endurance")]
    user_id = await _create_user("pipe-summary-noop@example.com", plan)
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(
            db, user_id, estimated_ftp=250, rider_type="allrounder",
            login_summary="Still valid summary.",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(db, user, plan, base_plan=plan, source="adapt")
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.login_summary == "Still valid summary."


@pytest.mark.asyncio
async def test_assessment_change_invalidates_login_summary():
    """An assessment-input change (no plan change) clears the login summary so it
    regenerates on load — the summary depends on the assessment, not just the
    plan (#370)."""
    from services import assessment_pipeline

    d = "2026-07-23"
    user_id = await _create_user("pipe-assess-summary@example.com", [_day(d, "endurance")])
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(
            db,
            user_id,
            estimated_ftp=250,
            rider_type="allrounder",
            login_summary="Old summary based on the previous FTP.",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        # Simulate an assessment-input write followed by the pipeline notify that
        # every such writer now performs (e.g. metrics recalc, .fit upload).
        await crud.upsert_rider_assessment(
            db, user_id, estimated_ftp=280, rider_type="allrounder",
        )
        await assessment_pipeline.notify_changed(db, user)
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.login_summary is None


@pytest.mark.asyncio
async def test_coach_chat_pins_day_against_automated_overwrite():
    """The reported bug: an automated trigger must not revert a user-set day (#342).

    A recovery day is changed to VO2max via coach chat (pinning it). A later
    ride-review adaptation proposing "easy recovery spin" must leave it alone.
    """
    d = "2026-08-01"
    base = [_day(d, "endurance")]
    user_id = await _create_user("pipe-pin@example.com", base)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        pinned = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "workoutType": "vo2max", "durationMinutes": 75}],
            base_plan=base,
            source="coach_chat",
        )
        await db.commit()
    assert next(x for x in pinned if x["date"] == d)["source"] == "user"

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        result = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "workoutType": "recovery", "durationMinutes": 30}],
            base_plan=current,
            source="ride_review",
        )
        await db.commit()

    day = next(x for x in result if x["date"] == d)
    assert day["workoutType"] == "vo2max"  # the user's pinned edit survives
    assert day["source"] == "user"


@pytest.mark.asyncio
async def test_automated_trigger_updates_unpinned_day():
    """An automated trigger may still change a day the user never touched."""
    d = "2026-08-02"
    base = [_day(d, "endurance")]
    user_id = await _create_user("pipe-unpinned@example.com", base)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "workoutType": "recovery", "durationMinutes": 30}],
            base_plan=base,
            source="ride_review",
        )
        await db.commit()

    day = next(x for x in result if x["date"] == d)
    assert day["workoutType"] == "recovery"
    assert day["source"] == "ride_review"


@pytest.mark.asyncio
async def test_generate_respects_user_pin():
    """A ``generate`` write must not overwrite a user-pinned day (#359).

    ``generate`` has no manual UI caller — the frontend fires it automatically
    during activity sync — so it is a pin-respecting trigger. A full replan that
    proposes a different workout for a coach-chat-pinned day must leave it alone.
    """
    d = "2026-08-03"
    user_id = await _create_user("pipe-generate@example.com", [_day(d, "endurance")])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "workoutType": "vo2max", "durationMinutes": 75}],
            base_plan=[_day(d, "endurance")],
            source="coach_chat",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        result = await plan_pipeline.commit_plan(
            db, user, [_day(d, "rest", duration=0)], base_plan=current,
            source="generate",
        )
        await db.commit()

    day = next(x for x in result if x["date"] == d)
    assert day["workoutType"] == "vo2max"  # the user's pinned edit survives
    assert day["source"] == "user"


@pytest.mark.asyncio
async def test_adapt_on_stale_plan_preserves_pinned_day():
    """The reported bug: on-load ``adapt`` must not revert a coach-chat pin (#359).

    The frontend auto-fires ``adapt`` on a stale-plan dashboard load. A pinned day
    that the adaptation proposes to change must survive.
    """
    d = "2026-08-04"
    user_id = await _create_user("pipe-adapt-pin@example.com", [_day(d, "endurance")])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "workoutType": "vo2max", "durationMinutes": 75}],
            base_plan=[_day(d, "endurance")],
            source="coach_chat",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        result = await plan_pipeline.commit_plan(
            db, user, [_day(d, "recovery", duration=30)], base_plan=current,
            source="adapt",
        )
        await db.commit()

    day = next(x for x in result if x["date"] == d)
    assert day["workoutType"] == "vo2max"  # the user's pinned edit survives
    assert day["source"] == "user"


@pytest.mark.asyncio
async def test_generate_preserves_omitted_pinned_day():
    """A full replan that drops a pinned future date must keep that day (#359).

    ``generate`` can shift the plan window and omit a pinned day entirely;
    ``_preserve_pinned_days`` re-appends it so the user's edit is never lost.
    """
    pinned = "2026-08-06"
    other = "2026-08-07"
    user_id = await _create_user(
        "pipe-generate-omit@example.com", [_day(pinned, "endurance"), _day(other, "endurance")]
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": pinned, "workoutType": "vo2max", "durationMinutes": 75}],
            base_plan=[_day(pinned, "endurance"), _day(other, "endurance")],
            source="coach_chat",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        # The replan omits the pinned date entirely.
        result = await plan_pipeline.commit_plan(
            db, user, [_day(other, "tempo")], base_plan=current,
            source="generate",
        )
        await db.commit()

    day = next((x for x in result if x["date"] == pinned), None)
    assert day is not None  # the omitted pinned day is restored
    assert day["workoutType"] == "vo2max"
    assert day["source"] == "user"


@pytest.mark.asyncio
async def test_automated_full_plan_cannot_overwrite_completed_day():
    """A background full-plan write must not rewrite a completed day (#345)."""
    d = "2026-08-05"
    completed = _day(d, "intervals", completed=True)
    user_id = await _create_user("pipe-completed@example.com", [completed])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await plan_pipeline.commit_plan(
            db,
            user,
            [_day(d, "recovery", duration=30)],  # nightly LLM proposes a change
            base_plan=[completed],
            source="nightly_maintenance",
        )
        await db.commit()

    day = next(x for x in result if x["date"] == d)
    assert day["workoutType"] == "intervals"  # the completed day is untouched
    assert day["completed"] is True


@pytest.mark.asyncio
async def test_automated_full_plan_cannot_drop_completed_day():
    """A completed day omitted by the proposal must be kept, not dropped (#345)."""
    done = _day("2026-08-06", "endurance", completed=True)
    future = _day("2026-08-07", "endurance")
    user_id = await _create_user("pipe-completed-drop@example.com", [done, future])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await plan_pipeline.commit_plan(
            db,
            user,
            [_day("2026-08-07", "intervals")],  # proposal omits the completed day
            base_plan=[done, future],
            source="nightly_maintenance",
        )
        await db.commit()

    dates = {x["date"] for x in result}
    assert "2026-08-06" in dates  # completed day survives
    assert next(x for x in result if x["date"] == "2026-08-07")["workoutType"] == "intervals"


@pytest.mark.asyncio
async def test_user_edit_can_still_modify_completed_day():
    """Completed-day protection is only for automated triggers, not user edits."""
    d = "2026-08-08"
    completed = _day(d, "intervals", completed=True)
    user_id = await _create_user("pipe-completed-user@example.com", [completed])

    edited = {**_day(d, "endurance", completed=True), "feedback": {"perceivedEffort": 7}}
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await plan_pipeline.commit_plan(
            db, user, [edited], base_plan=[completed], source="user_edit"
        )
        await db.commit()

    day = next(x for x in result if x["date"] == d)
    assert day["workoutType"] == "endurance"  # the user's correction is applied
    assert day["feedback"] == {"perceivedEffort": 7}


@pytest.mark.asyncio
async def test_automated_full_plan_keeps_trained_day_without_completed_flag():
    """A day the athlete rode but never hand-ticked must survive an automated
    regenerate whose window omits it (#468 follow-up).

    The ``completed`` flag is set only when the athlete ticks a workout by hand;
    a synced ride never sets it. Before this fix such a day was protected by
    neither the pin guard nor the completed guard, so a full regenerate starting
    a day later dropped it and the dashboard showed "No planned workout found"
    for the completed ride.
    """
    ridden = _day("2026-08-09", "endurance")  # completed flag stays False
    future = _day("2026-08-10", "endurance")
    user_id = await _create_user("pipe-trained-drop@example.com", [ridden, future])

    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=92001,
            activity_date="2026-08-09", duration_seconds=3600,
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await plan_pipeline.commit_plan(
            db,
            user,
            [_day("2026-08-10", "intervals")],  # regenerate omits the ridden day
            base_plan=[ridden, future],
            source="generate",
        )
        await db.commit()

    day = next((x for x in result if x["date"] == "2026-08-09"), None)
    assert day is not None  # the trained day is not dropped
    assert day["workoutType"] == "endurance"  # and not rewritten


@pytest.mark.asyncio
async def test_automated_full_plan_still_drops_untrained_past_day():
    """Activity-day protection must not freeze the whole past: a day with no
    recorded activity still rolls off when an automated regenerate omits it."""
    stale = _day("2026-08-11", "endurance")
    future = _day("2026-08-12", "endurance")
    user_id = await _create_user("pipe-untrained-drop@example.com", [stale, future])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await plan_pipeline.commit_plan(
            db, user, [_day("2026-08-12", "intervals")],
            base_plan=[stale, future], source="generate",
        )
        await db.commit()

    assert all(x["date"] != "2026-08-11" for x in result)  # no activity -> rolls off


@pytest.mark.asyncio
async def test_unknown_source_is_rejected():
    """A typo'd source must fail loudly rather than silently mis-pin."""
    user_id = await _create_user("pipe-badsource@example.com", [_day("2026-08-04")])
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        with pytest.raises(ValueError):
            await plan_pipeline.commit_plan(
                db, user, [_day("2026-08-04")], base_plan=[_day("2026-08-04")],
                source="not_a_real_trigger",
            )


@pytest.mark.asyncio
async def test_plan_change_records_day_history():
    """An applied change appends a history row with the trigger and diff (#343)."""
    d = "2026-09-01"
    base = [_day(d, "endurance")]
    user_id = await _create_user("hist-applied@example.com", base)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "workoutType": "vo2max", "durationMinutes": 75}],
            base_plan=base,
            source="coach_chat",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        rows = await crud.list_plan_day_history(db, user_id)

    assert len(rows) == 1
    row = rows[0]
    assert row.date == d
    assert row.source == "coach_chat"  # the real trigger, not the "user" stamp
    assert row.applied is True
    assert row.old_day["workoutType"] == "endurance"
    assert row.new_day["workoutType"] == "vo2max"


@pytest.mark.asyncio
async def test_blocked_automated_change_records_unapplied_history():
    """A pin-blocked automated change is logged with applied=False (#343)."""
    d = "2026-09-02"
    base = [_day(d, "endurance")]
    user_id = await _create_user("hist-blocked@example.com", base)

    # User pins the day via coach chat.
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db, user, [{"date": d, "workoutType": "vo2max", "durationMinutes": 75}],
            base_plan=base, source="coach_chat",
        )
        await db.commit()

    # Automated ride-review tries to revert it — blocked, but logged.
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        await plan_pipeline.commit_plan_updates(
            db, user, [{"date": d, "workoutType": "recovery", "durationMinutes": 30}],
            base_plan=current, source="ride_review",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        blocked = [
            r for r in await crud.list_plan_day_history(db, user_id) if not r.applied
        ]

    assert len(blocked) == 1
    assert blocked[0].source == "ride_review"
    assert blocked[0].old_day["workoutType"] == "vo2max"  # what stayed
    assert blocked[0].new_day["workoutType"] == "recovery"  # what was wanted


@pytest.mark.asyncio
async def test_noop_plan_write_records_no_history():
    """An unchanged write must not append history rows."""
    d = "2026-09-03"
    plan = [_day(d, "endurance")]
    user_id = await _create_user("hist-noop@example.com", plan)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(db, user, plan, base_plan=plan, source="adapt")
        await db.commit()

    async with TestSessionLocal() as db:
        rows = await crud.list_plan_day_history(db, user_id)

    assert rows == []


@pytest.mark.asyncio
async def test_summary_regenerate_persists(monkeypatch):
    """summary_pipeline.regenerate writes a freshly generated summary."""
    d = "2026-07-22"
    user_id = await _create_user("pipe-regen@example.com", [_day(d, "endurance")])
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(
            db, user_id, estimated_ftp=250, rider_type="allrounder",
        )
        await db.commit()

    async def fake_generate(**_):
        return "Fresh summary built from the current plan."

    monkeypatch.setattr(
        summary_pipeline.ai_service, "generate_login_summary", fake_generate
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await summary_pipeline.regenerate(db, user, provider="gemini")
        await db.commit()

    assert result == "Fresh summary built from the current plan."
    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.login_summary == "Fresh summary built from the current plan."


@pytest.mark.asyncio
async def test_plan_change_refreshes_ride_snapshot():
    """A plan change re-matches rides on the changed date, refreshing the stale
    ``matched_plan_snapshot`` so the dashboard fallback stays correct (#364)."""
    from services.ride_matching import apply_ride_plan_matches

    d = "2026-07-02"
    user_id = await _create_user("pipe-ridesnap@example.com", [_day(d, "endurance")])

    # Import a ride for that date and match it against the original plan.
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=91001, activity_date=d,
            duration_seconds=3600,
        )
        await db.commit()
    async with TestSessionLocal() as db:
        plan_row = await crud.get_training_plan(db, user_id)
        await apply_ride_plan_matches(db, user_id, plan_row.plan, [91001])
        await db.commit()
    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_activity_ids(db, user_id, [91001])
        assert rides[0].matched_plan_snapshot["workoutType"] == "endurance"

    # Coach edits the same day -> the plan change must fan out to the ride-match
    # pipeline and refresh the already-imported ride's snapshot.
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db, user,
            [{"date": d, "workoutType": "intervals", "title": "New Intervals",
              "description": "Session", "durationMinutes": 75}],
            base_plan=[_day(d, "endurance")],
            source="coach_chat",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_activity_ids(db, user_id, [91001])
        assert rides[0].matched_plan_snapshot["workoutType"] == "intervals"
        assert rides[0].matched_plan_date == d


@pytest.mark.asyncio
async def test_matched_ride_marks_plan_day_completed():
    """A synced ride that auto-matches its plan day marks that day completed, so
    completed-day protection covers it without a manual tick (#472 follow-up)."""
    from services.ride_matching import (
        apply_ride_plan_matches,
        mark_matched_days_completed,
    )

    d = "2026-07-05"
    user_id = await _create_user(
        "pipe-complete-on-import@example.com", [_day(d, "endurance")]
    )

    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=93001, activity_date=d,
            duration_seconds=3600,
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        auto_matched = await apply_ride_plan_matches(db, user_id, plan, [93001])
        await mark_matched_days_completed(db, user, plan, auto_matched)
        await db.commit()

    async with TestSessionLocal() as db:
        day = next(
            x for x in (await crud.get_training_plan(db, user_id)).plan
            if x["date"] == d
        )
        assert day["completed"] is True


@pytest.mark.asyncio
async def test_completed_matched_day_survives_later_regenerate():
    """The completion set on import must let the existing completed-day guard keep
    the day when a later automated regenerate omits it (#472 follow-up)."""
    from services.ride_matching import (
        apply_ride_plan_matches,
        mark_matched_days_completed,
    )

    d = "2026-07-06"
    future = "2026-07-07"
    user_id = await _create_user(
        "pipe-complete-survives@example.com", [_day(d, "endurance"), _day(future)]
    )
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=93002, activity_date=d,
            duration_seconds=3600,
        )
        await db.commit()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        auto_matched = await apply_ride_plan_matches(db, user_id, plan, [93002])
        await mark_matched_days_completed(db, user, plan, auto_matched)
        await db.commit()

    # A regenerate whose window starts the next day omits the completed day.
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        base = (await crud.get_training_plan(db, user_id)).plan
        result = await plan_pipeline.commit_plan(
            db, user, [_day(future, "intervals")], base_plan=base, source="generate"
        )
        await db.commit()

    assert next(x for x in result if x["date"] == d)["workoutType"] == "endurance"


@pytest.mark.asyncio
async def test_activity_day_workout_frozen_but_completion_passes():
    """A trained day's workout stays frozen against an automated rewrite, but a
    completion marker on it still lands (#472 activity guard + #473 coexistence)."""
    d = "2026-07-08"
    user_id = await _create_user(
        "pipe-activity-coexist@example.com", [_day(d, "endurance", duration=90)]
    )
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db, user_id, strava_activity_id=93003, activity_date=d,
            duration_seconds=3600,
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        base = (await crud.get_training_plan(db, user_id)).plan
        # An automated trigger tries to both rewrite the trained day and mark it done.
        result = await plan_pipeline.commit_plan_updates(
            db, user,
            [{"date": d, "workoutType": "intervals", "durationMinutes": 30,
              "completed": True}],
            base_plan=base, source="nightly_maintenance",
        )
        await db.commit()

    day = next(x for x in result if x["date"] == d)
    assert day["workoutType"] == "endurance"  # workout frozen (day was trained)
    assert day["durationMinutes"] == 90
    assert day["completed"] is True           # completion still lands


@pytest.mark.asyncio
async def test_no_op_plan_write_does_not_refresh_snapshots():
    """A no-op plan write must not re-match rides (no changed dates fan out)."""
    from unittest.mock import AsyncMock

    from services import ride_match_pipeline

    d = "2026-07-03"
    plan = [_day(d, "endurance")]
    user_id = await _create_user("pipe-ridesnap-noop@example.com", plan)

    called = AsyncMock()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        # Patch the module-level refresh so we can assert it is never invoked.
        original = ride_match_pipeline.refresh
        ride_match_pipeline.refresh = called
        try:
            await plan_pipeline.commit_plan(
                db, user, plan, base_plan=plan, source="adapt"
            )
        finally:
            ride_match_pipeline.refresh = original
        await db.commit()

    called.assert_not_called()


def test_apply_plan_updates_scalar_replaces_stale_window():
    """A scalar duration update drops the base day's stale window (#422).

    Otherwise ``normalize_duration_fields`` would later crush the new scalar
    back to the leftover window's midpoint.
    """
    d = "2026-07-18"
    base = [
        {
            **_day(d, "endurance", duration=120),
            "durationMinMinutes": 45,
            "durationMaxMinutes": 60,
        }
    ]
    result = plan_pipeline.apply_plan_updates(
        base, [{"date": d, "durationMinutes": 180}]
    )
    assert result is not None
    day = next(x for x in result if x["date"] == d)
    assert day["durationMinutes"] == 180
    assert "durationMinMinutes" not in day
    assert "durationMaxMinutes" not in day


def test_apply_plan_updates_window_replaces_stale_scalar():
    """A window duration update drops the base day's stale scalar (#422)."""
    d = "2026-07-18"
    base = [_day(d, "endurance", duration=120)]
    result = plan_pipeline.apply_plan_updates(
        base,
        [{"date": d, "durationMinMinutes": 180, "durationMaxMinutes": 240}],
    )
    assert result is not None
    day = next(x for x in result if x["date"] == d)
    assert day["durationMinMinutes"] == 180
    assert day["durationMaxMinutes"] == 240
    # The stale single-value scalar (120) is replaced by the window midpoint;
    # apply_plan_updates now canonicalizes through PlanDay.
    assert day["durationMinutes"] == round((180 + 240) / 2)  # 210


def test_apply_plan_updates_keeps_duration_when_update_omits_it():
    """A description-only update leaves an untouched duration in place."""
    d = "2026-07-18"
    base = [_day(d, "endurance", duration=120)]
    result = plan_pipeline.apply_plan_updates(
        base, [{"date": d, "description": "Ride for 3 hours"}]
    )
    assert result is not None
    day = next(x for x in result if x["date"] == d)
    assert day["description"] == "Ride for 3 hours"
    assert day["durationMinutes"] == 120


def test_apply_plan_updates_drops_invalid_update_keeps_day():
    """A malformed update is dropped (not raised), leaving the day unchanged."""
    d = "2026-07-18"
    base = [_day(d, "endurance", duration=120)]
    result = plan_pipeline.apply_plan_updates(
        base, [{"date": d, "durationMinutes": "lots"}]  # non-numeric → invalid
    )
    assert result is not None
    day = next(x for x in result if x["date"] == d)
    assert day["durationMinutes"] == 120  # base day preserved


@pytest.mark.asyncio
async def test_commit_plan_updates_scalar_duration_change_persists():
    """End-to-end: a coach duration change lands coherently, not crushed (#422).

    Reproduces the prod bug where a day carrying a stale window (45/60) kept its
    old 120-minute scalar after a coach update asking for 3 hours.
    """
    d = (app_today() + timedelta(days=1)).isoformat()
    base = [
        {
            **_day(d, "endurance", duration=120),
            "durationMinMinutes": 45,
            "durationMaxMinutes": 60,
        }
    ]
    user_id = await _create_user("pipe-duration@example.com", base)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "durationMinutes": 180, "description": "Ride for 3 hours"}],
            base_plan=base,
            source="coach_chat",
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["durationMinutes"] == 180
    assert "durationMinMinutes" not in day
    assert "durationMaxMinutes" not in day


@pytest.mark.asyncio
async def test_incoherent_stored_day_is_backfilled_on_next_write():
    """The PlanDay gate lazily fixes a legacy incoherent stored day on next write.

    No migration: a day carrying a stale window (45/60) alongside a mismatched
    scalar (120) is normalized to the coherent midpoint the next time the plan is
    persisted, rather than being masked as a no-op.
    """
    d = (app_today() + timedelta(days=2)).isoformat()
    incoherent = {
        "date": d,
        "workoutType": "endurance",
        "title": f"Workout {d}",
        "description": "Session",
        "durationMinutes": 120,
        "durationMinMinutes": 45,
        "durationMaxMinutes": 60,
    }
    # Seed the incoherent day directly via crud, bypassing the pipeline gate.
    user_id = await _create_user("pipe-backfill@example.com", [dict(incoherent)])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan(
            db, user, [dict(incoherent)], base_plan=[dict(incoherent)], source="adapt"
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["durationMinMinutes"] == 45
    assert day["durationMaxMinutes"] == 60
    assert day["durationMinutes"] == round((45 + 60) / 2)  # 52 — now coherent
