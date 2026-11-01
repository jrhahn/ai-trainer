"""A sentence that points at a session the plan no longer has is struck
(ai-trainer-ops#235, the owner's decision of 2026-10-31)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import crud
from services import plan_pipeline
from services.dates import app_today
from services.stale_mentions import without_stale_mentions
from tests.factories import create_planned_user

TODAY = "2026-10-31"  # a Saturday


def _day(when: str, minutes: int, text: str, **extra) -> dict:
    return {
        "date": when,
        "workoutType": "endurance" if minutes else "rest",
        "title": "Ride" if minutes else "Rest Day",
        "durationMinutes": minutes,
        "description": text,
        **extra,
    }


def _tom(**texts) -> list[dict]:
    """Tom's first week (Mon, Wed, Fri) after his Wednesday moved to Thursday."""
    minutes = {"02": 60, "03": 0, "04": 0, "05": 75, "06": 45, "07": 0, "08": 0}
    return [
        _day(f"2026-11-{dd}", m, texts.get(f"d{dd}", "Plain text."))
        for dd, m in minutes.items()
    ]


def _text(plan: list[dict], when: str, field: str = "description"):
    return next(d for d in plan if d["date"] == when)[field]


def test_a_sentence_naming_a_session_on_a_day_now_rest_is_struck():
    """Tom's Tuesday after the move, from the persona run of 2026-10-31."""
    plan = _tom(
        d03="Take a rest day today between your Monday and Wednesday sessions. "
        "Let your muscles repair."
    )

    assert _text(without_stale_mentions(plan, TODAY), "2026-11-03") == (
        "Let your muscles repair."
    )


def test_a_weekend_with_no_session_on_either_day_is_gone_from_the_neighbours():
    """The guard's case: Tom does not ride at weekends (#235)."""
    plan = _tom(
        d06="Easy spin. This sets up your legs nicely for the weekend block.",
        d08="Recover from a big weekend of base riding.",
    )

    kept = without_stale_mentions(plan, TODAY)

    assert _text(kept, "2026-11-06") == "Easy spin."
    # Nothing left of it: a rest day says so.
    assert _text(kept, "2026-11-08") == "Rest day."


def test_a_mention_of_a_day_that_has_its_session_stays():
    plan = _tom(
        d07="Enjoy a rest day today following your Friday ride.",
        d03="Rest today. Yesterday's ride was the first; Thursday's session is next.",
    )
    plan[4]["description"] = "Ride easy. Weekend rides are off for you."
    plan[5] = _day("2026-11-07", 90, "Long ride.")

    kept = without_stale_mentions(plan, TODAY)

    assert kept == plan


@pytest.mark.parametrize(
    "text",
    [
        # True when Wednesday is rest: "training" is not a session word here.
        "Wednesday is not one of your training days.",
        # A rest, not a session.
        "Wednesday's rest has you fresh.",
        # Its own weekday: no other Tuesday within six days.
        "Tuesday's session is easy.",
    ],
)
def test_a_sentence_that_names_no_lost_session_stays(text):
    plan = _tom(d03=text)

    assert _text(without_stale_mentions(plan, TODAY), "2026-11-03") == text


def test_a_day_the_plan_does_not_hold_says_nothing_either_way():
    """Monday's ride before the plan starts may well have happened."""
    plan = _tom(d02="Ride for an hour. Sunday's long ride is still in your legs.")[:1]

    assert without_stale_mentions(plan, TODAY) == plan


def test_yesterday_and_tomorrow_mean_exactly_that_day():
    plan = _tom(
        d04="Recover from yesterday's ride.",
        d07="Tomorrow's long ride needs fresh legs.",
    )

    kept = without_stale_mentions(plan, TODAY)

    assert _text(kept, "2026-11-04") == "Rest day."
    assert _text(kept, "2026-11-07") == "Rest day."


def test_a_session_left_without_words_keeps_its_title():
    plan = _tom(d05="Moved from Wednesday's session slot.")

    assert _text(without_stale_mentions(plan, TODAY), "2026-11-05") == "Ride"


def test_purpose_and_focus_points_are_cleaned_too():
    plan = _tom()
    plan[1].update(
        workoutPurpose="Absorb Monday's ride. Prepare for Wednesday's intervals.",
        keyFocusPoints=["Sleep well", "Fresh legs for the weekend ride"],
    )

    kept = next(
        d for d in without_stale_mentions(plan, TODAY) if d["date"] == "2026-11-03"
    )

    assert kept["workoutPurpose"] == "Absorb Monday's ride."
    assert kept["keyFocusPoints"] == ["Sleep well"]


def test_today_the_past_done_and_protected_days_keep_their_words():
    stale = "Rest between your Monday and Wednesday sessions."
    plan = _tom(d03=stale)

    assert without_stale_mentions(plan, "2026-11-03") == plan
    assert without_stale_mentions(plan, TODAY, protected={"2026-11-03"}) == plan
    plan[1]["completed"] = True
    assert without_stale_mentions(plan, TODAY) == plan


def _week_from_next_monday() -> list[str]:
    monday = app_today() + timedelta(days=7 - app_today().weekday())
    return [(monday + timedelta(days=offset)).isoformat() for offset in range(7)]


@pytest.mark.asyncio
async def test_a_move_in_the_chat_strikes_the_neighbour_and_keeps_the_coachs_words(db):
    """The coach moves Wednesday's ride to Thursday. Tuesday still spoke of the
    Wednesday session; Thursday's new text speaks of the move and stays."""
    mon, tue, wed, thu, *_ = _week_from_next_monday()
    plan = [
        _day(mon, 60, "Easy ride."),
        _day(tue, 0, "Rest between your Monday and Wednesday sessions. Sleep well."),
        _day(wed, 60, "Steady ride."),
        _day(thu, 0, "Rest."),
    ]
    user_id = await create_planned_user("stale-235@example.com", plan)
    user = await crud.get_user_by_id(db, user_id)

    written = await plan_pipeline.commit_plan_updates(
        db,
        user,
        [
            {
                "date": wed,
                "workoutType": "rest",
                "durationMinutes": 0,
                "description": "Rest.",
            },
            {
                "date": thu,
                "workoutType": "endurance",
                "durationMinutes": 60,
                "description": "Moved here from Wednesday's session.",
            },
        ],
        base_plan=plan,
        source="coach_chat",
    )

    by_date = {d["date"]: d for d in written.plan}
    assert by_date[tue]["description"] == "Sleep well."
    assert by_date[thu]["description"] == "Moved here from Wednesday's session."


@pytest.mark.asyncio
async def test_a_new_plan_loses_the_weekend_talk_its_guard_made_stale(db):
    """A generated plan, its weekend made rest before the commit (#189)."""
    mon, *_middle, fri, sat, sun = _week_from_next_monday()
    user_id = await create_planned_user("stale-235-gen@example.com", [])
    user = await crud.get_user_by_id(db, user_id)
    plan = [
        _day(mon, 60, "Easy ride."),
        _day(fri, 45, "Easy spin. It sets up your legs for the weekend block."),
        _day(sat, 0, "Rest Day"),
        _day(sun, 0, "Rest Day"),
    ]

    written = await plan_pipeline.commit_plan(
        db, user, plan, base_plan=[], source="generate"
    )

    assert {d["date"]: d for d in written.plan}[fri]["description"] == "Easy spin."
    assert date.fromisoformat(fri).weekday() == 4


def test_a_write_that_takes_no_session_leaves_an_old_stale_sentence():
    """A sentence that was stale before stays on a day the write does not
    touch: a nightly run that changes nothing must change no text, or the
    athlete is told about it in the chat."""
    plan = _tom(d03="Rest between your Monday and Wednesday sessions.")

    assert without_stale_mentions(plan, TODAY, before=plan) == plan


def test_a_neighbour_that_loses_its_session_now_strikes_the_sentence():
    before = _tom(d03="Rest between your Monday and Wednesday sessions. Sleep.")
    before[2] = _day("2026-11-04", 60, "Steady ride.")
    after = [dict(day) for day in before]
    after[2] = _day("2026-11-04", 0, "Rest.")

    kept = without_stale_mentions(after, TODAY, before=before)

    assert _text(kept, "2026-11-03") == "Sleep."


def test_a_day_with_new_words_is_read_in_full():
    """The writer gave Friday new words that speak of a weekend nobody rides."""
    before = _tom()
    after = [dict(day) for day in before]
    after[4] = _day("2026-11-06", 45, "Easy spin. It sets up the weekend block.")

    kept = without_stale_mentions(after, TODAY, before=before)

    assert _text(kept, "2026-11-06") == "Easy spin."


@pytest.mark.asyncio
async def test_a_nightly_run_that_changes_nothing_changes_no_text(db):
    mon, tue, wed, *_ = _week_from_next_monday()
    plan = [
        _day(mon, 60, "Easy ride."),
        _day(tue, 0, "Rest between your Monday and Wednesday sessions."),
        _day(wed, 0, "Rest."),
    ]
    user_id = await create_planned_user("stale-235-nightly@example.com", plan)
    user = await crud.get_user_by_id(db, user_id)
    stored = (await crud.get_training_plan(db, user_id)).plan

    written = await plan_pipeline.commit_plan(
        db, user, stored, base_plan=stored, source="nightly_maintenance"
    )

    assert written.applied_changes == []
    assert {d["date"]: d for d in written.plan}[tue]["description"] == (
        "Rest between your Monday and Wednesday sessions."
    )


def test_a_past_session_on_that_weekday_does_not_hide_the_stale_mention():
    """The stored plan keeps the days already gone; last Wednesday's ride is
    not the session Tuesday's sentence speaks of (review of #1234)."""
    plan = [
        _day("2026-10-28", 60, "Steady ride."),
        *_tom(d03="Rest between your Monday and Wednesday sessions. Sleep."),
    ]

    assert _text(without_stale_mentions(plan, TODAY), "2026-11-03") == "Sleep."


def test_today_is_no_evidence_either_way():
    """On the day it is, today's session is not taken by any write."""
    plan = _tom(d03="Monday's ride was a good start.")
    plan[0] = _day("2026-11-02", 0, "Rest.")

    assert without_stale_mentions(plan, "2026-11-02") == plan


def test_a_day_with_a_date_the_step_cannot_read_passes_unchanged():
    plan = [*_tom(), _day("next friday", 0, "Rest before your Saturday ride.")]

    assert without_stale_mentions(plan, TODAY)[-1] == plan[-1]


def test_a_purpose_or_focus_points_left_empty_are_absent():
    plan = _tom()
    plan[1].update(
        workoutPurpose="Prepare for Wednesday's intervals.",
        keyFocusPoints=["Fresh legs for Wednesday's session"],
    )

    kept = next(
        d for d in without_stale_mentions(plan, TODAY) if d["date"] == "2026-11-03"
    )

    assert kept["workoutPurpose"] is None
    assert kept["keyFocusPoints"] is None


def test_a_line_break_before_a_kept_sentence_stays():
    plan = _tom(d03="Rest today.\nWednesday's session is key.\nSleep well.")

    assert _text(without_stale_mentions(plan, TODAY), "2026-11-03") == (
        "Rest today.\nSleep well."
    )


def test_a_day_with_nothing_to_strike_is_left_exactly_as_it_was():
    """An empty focus list stays an empty list, and a day without a date is
    passed on: no text-only change without a strike (re-review of #1234)."""
    plan = _tom(d03="Rest between your Monday and Wednesday sessions.")
    plan[3]["keyFocusPoints"] = []
    plan.append({"workoutType": "rest", "description": "A day with no date."})

    kept = without_stale_mentions(plan, TODAY)

    assert kept[3] is plan[3]
    assert kept[-1] is plan[-1]
    assert _text(kept, "2026-11-03") == "Rest day."
