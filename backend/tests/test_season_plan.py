"""The season plan: week targets the daily plan fits into (ai-trainer-ops#98)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import crud
import models
from auth import hash_password
from services import ai_service, plan_context, plan_maintenance, plan_priors
from services import season_plan as sp
from services import training_phase as tp
# The real writer, bound before ``conftest`` stubs it for every other test.
from services.ai_service import write_week_goals
from tests.conftest import TestSessionLocal

JUNE = date(2026, 6, 8)
OCTOBER = date(2026, 10, 12)
DECEMBER = date(2026, 12, 7)


def _ridden(monday: date, weekly_hours: list[float]):
    """Daily rides over calendar weeks, oldest first, the last ending the Sunday before."""
    rows = []
    for back, hours in enumerate(reversed(weekly_hours)):
        for day in range(7):
            if hours:
                rows.append(
                    SimpleNamespace(
                        activity_date=(monday - timedelta(days=1 + back * 7 + day)).isoformat(),
                        duration_seconds=int(hours / 7 * 3600),
                    )
                )
    return rows


def _session(day: date, workout: str = "endurance", minutes: int = 60) -> dict:
    return {"date": day.isoformat(), "workoutType": workout, "durationMinutes": minutes}


def test_a_race_lays_out_build_peak_taper_and_the_break_after():
    race = OCTOBER + timedelta(days=76)
    weeks = sp.season_weeks(OCTOBER, [race], [], weekly_hours=8)

    assert [w.phase for w in weeks] == [tp.BUILD] * 5 + [tp.PEAK] * 5 + [tp.TAPER, tp.TRANSITION]
    assert weeks[0].focus == sp.EARLY_BUILD_FOCUS
    assert weeks[1].focus == sp.LATE_BUILD_FOCUS
    assert [i for i, w in enumerate(weeks) if w.recovery] == [3, 7]
    assert weeks[10].load_week is None and weeks[10].hours == (3.0, 5.0)


def test_three_ridden_load_weeks_make_this_week_the_recovery_week():
    weeks = sp.season_weeks(JUNE, [], _ridden(JUNE, [8, 8, 8, 8, 5, 8, 9, 10]), weekly_hours=10)

    assert weeks[0].recovery
    assert weeks[0].hours == (4.5, 5.5)
    assert weeks[0].hard_sessions == sp.RECOVERY_HARD_SESSIONS
    assert [w.load_week for w in weeks[1:4]] == [1, 2, 3]
    assert weeks[4].recovery


def test_a_target_grows_from_what_was_ridden_up_to_the_stated_hours():
    weeks = sp.season_weeks(JUNE, [], _ridden(JUNE, [3] * 8), weekly_hours=8)
    tops = [w.hours[1] for w in weeks if not w.recovery]

    # Inside what the ramp prior would let through from the 3 h ridden.
    assert weeks[1].hours[1] < 3 * plan_priors.RAMP_RATIO_MAX
    assert tops == sorted(tops)
    assert max(tops) < 8


def test_a_new_account_plans_inside_its_stated_hours():
    weeks = sp.season_weeks(JUNE, [], [], weekly_hours=6)

    assert weeks[0].load_week == 1
    assert weeks[0].hours == (5.0, 6.0)
    assert sp.season_weeks(JUNE, [], [])[0].hours is None


def test_a_second_race_leaves_the_weeks_before_the_first_alone():
    first = JUNE + timedelta(days=4 * 7 + 6)
    second = JUNE + timedelta(days=10 * 7 + 6)
    before = sp.season_weeks(JUNE, [first], [], weekly_hours=8)
    after = sp.season_weeks(JUNE, [first, second], [], weekly_hours=8)

    changed = [
        i for i, (a, b) in enumerate(zip(before, after)) if sp.fingerprint(a) != sp.fingerprint(b)
    ]
    assert changed
    assert min(changed) == 5


def test_a_maintenance_block_holds_for_the_whole_calendar_week():
    foci = {tp.training_phase(JUNE + timedelta(days=d), [], []).focus for d in range(7)}

    assert len(foci) == 1


def test_a_week_over_its_hours_and_hard_sessions_is_reported():
    weeks = sp.season_weeks(JUNE, [], [], weekly_hours=6)
    plan = [_session(JUNE + timedelta(days=d), "intervals", 90) for d in range(5)] + [
        _session(JUNE + timedelta(days=5), "endurance", 120),
        _session(JUNE + timedelta(days=6), "rest", 0),
    ]

    text = " ".join(d.statement for d in sp.find_week_departures(plan, weeks, JUNE))

    assert "plans 9.5 h; its target is 5-6 h" in text
    assert "plans 5 hard session(s)" in text
    assert "asks for 1-2 hard sessions" in text
    assert "no ride of" not in text


def test_a_base_week_without_a_long_ride_misses_its_key_session():
    weeks = sp.season_weeks(DECEMBER, [], [], weekly_hours=8)
    plan = [_session(DECEMBER + timedelta(days=d)) for d in range(7)]

    found = sp.find_week_departures(plan, weeks, DECEMBER)

    assert weeks[0].phase == tp.BASE
    assert [d.statement for d in found] == [
        "The week from 2026-12-07 has no ride of 1.6 h or longer; its key session "
        "is one long endurance ride."
    ]


def test_a_race_is_neither_hours_nor_a_hard_session():
    race = JUNE + timedelta(days=6)
    weeks = sp.season_weeks(JUNE, [race], [], weekly_hours=8)
    plan = [_session(JUNE + timedelta(days=d), "endurance", 45) for d in range(6)]
    plan.append(_session(race, "race", 300))

    assert weeks[0].phase == tp.TAPER
    assert sp.find_week_departures(plan, weeks, JUNE) == []


def test_the_current_week_is_judged_from_today_when_its_past_days_are_gone():
    # A regeneration on Wednesday keeps no untrained past days.
    wednesday = DECEMBER + timedelta(days=2)
    weeks = sp.season_weeks(wednesday, [], [], weekly_hours=8)
    hard = [_session(wednesday + timedelta(days=d), "intervals") for d in range(5)]
    easy = [_session(wednesday + timedelta(days=d)) for d in range(5)]

    over = " ".join(d.statement for d in sp.find_week_departures(hard, weeks, wednesday))
    under = sp.find_week_departures(easy, weeks, wednesday)

    # Five hard sessions from Wednesday are too many whatever Monday held.
    assert "The week from 2026-12-07 plans 5 hard session(s)" in over
    # A long ride may have been on Monday: too little is not judged on part of a week.
    assert under == []


def test_a_week_the_plan_does_not_cover_is_not_judged():
    weeks = sp.season_weeks(JUNE, [], [], weekly_hours=6)
    plan = [_session(JUNE + timedelta(days=d), "intervals", 200) for d in range(3)]

    assert sp.find_week_departures(plan, weeks, JUNE) == []


def test_the_section_states_the_plan_window_in_full_and_the_rest_in_a_line():
    weeks = sp.season_weeks(JUNE, [], [], weekly_hours=6)
    departures = [sp.WeekDeparture(JUNE, "The week from 2026-06-08 plans 9.5 h.")]

    section = sp.season_section(weeks, {JUNE: "Settle into the rhythm."}, departures, JUNE)
    window, later = section.split("- Then: ")

    assert "- This week from 2026-06-08: maintenance, load week 1 of 3." in window
    assert 'Goal: "Settle into the rhythm."' in window
    assert "- Week from 2026-06-15:" in window
    assert "2026-06-22" not in window
    assert later.startswith("2026-06-22 maintenance, load week 3 of 3;")
    assert "  - The week from 2026-06-08 plans 9.5 h." in later
    assert "tell the athlete why" in later


def test_a_goal_written_for_another_week_is_not_the_goal():
    week = sp.season_weeks(JUNE, [], [], weekly_hours=6)[0]
    stale = SimpleNamespace(fingerprint="build|x|load|-", goal="Old")
    fresh = SimpleNamespace(fingerprint=sp.fingerprint(week), goal="New")

    assert sp.current_goals([week], {JUNE.isoformat(): stale}) == {}
    assert sp.current_goals([week], {JUNE.isoformat(): fresh}) == {JUNE: "New"}


@pytest.mark.asyncio
async def test_the_goal_writer_keeps_only_the_weeks_it_was_asked_for(monkeypatch):
    async def chat(*args, **kwargs):
        return json.dumps(
            {
                "goals": [
                    {"weekStart": "2026-06-08", "goal": "  Settle\n into   the rhythm. "},
                    {"weekStart": "2026-06-15", "goal": ""},
                    {"weekStart": "2030-01-07", "goal": "A week nobody asked for."},
                    "junk",
                ]
            }
        )

    monkeypatch.setattr(ai_service, "_chat", chat)
    goals = await write_week_goals(
        [{"weekStart": "2026-06-08", "summary": "a"}, {"weekStart": "2026-06-15", "summary": "b"}],
        {},
    )

    assert goals == {"2026-06-08": "Settle into the rhythm."}


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_goals(monkeypatch):
    calls: list[list[str]] = []

    async def write_week_goals(weeks, profile, **kwargs):
        calls.append([w["weekStart"] for w in weeks])
        return {w["weekStart"]: f"Goal for {w['weekStart']}" for w in weeks}

    monkeypatch.setattr(ai_service, "write_week_goals", write_week_goals)
    return calls


@pytest.mark.asyncio
async def test_goals_are_written_once_and_again_only_for_weeks_that_moved(
    client, auth_headers, fake_goals
):
    resp = await client.get("/api/v1/ai/season-plan", headers=auth_headers)

    assert resp.status_code == 200
    weeks = resp.json()["weeks"]
    assert len(weeks) == sp.SEASON_WEEKS
    assert all(w["goal"] is None for w in weeks)

    written = (await client.post("/api/v1/ai/season-plan/goals", headers=auth_headers)).json()
    assert all(w["goal"] == f"Goal for {w['weekStart']}" for w in written["weeks"])
    await client.post("/api/v1/ai/season-plan/goals", headers=auth_headers)
    assert [len(c) for c in fake_goals] == [sp.SEASON_WEEKS]

    race = date.fromisoformat(weeks[6]["weekStart"]) + timedelta(days=6)
    await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": race.isoformat(), "distanceKm": 100, "elevationM": 1000},
    )
    after = (await client.post("/api/v1/ai/season-plan/goals", headers=auth_headers)).json()

    assert len(fake_goals) == 2
    assert 0 < len(fake_goals[1]) < sp.SEASON_WEEKS
    assert weeks[6]["weekStart"] in fake_goals[1]
    assert all(w["goal"] for w in after["weeks"])


@pytest.mark.asyncio
async def test_every_plan_writer_is_told_the_weeks(client, auth_headers, mock_ai_service):
    response = await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})

    assert response.status_code == 200
    section = mock_ai_service["generate_training_plan"].call_args.kwargs["athlete_model_section"]
    assert "Season plan (week targets" in section
    me = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    async with TestSessionLocal() as db:
        context = await plan_context.plan_writer_context(db, me["id"], [])
    assert "season" in dict(context.sections)


async def _nightly_user(plan: list[dict], fitted: str | None) -> str:
    """A user whose plan covers this week and the next, with this week's goal stored."""
    async with TestSessionLocal() as db:
        user = models.User(
            email="season-nightly@example.com",
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, plan)
        week = sp.season_weeks(JUNE, [], [])[0]
        row = await crud.save_week_goal(
            db, user.id, JUNE.isoformat(), fingerprint=sp.fingerprint(week), goal="Goal"
        )
        row.fitted_fingerprint = fitted or sp.fingerprint(week)
        await db.commit()
        return user.id


@pytest.mark.asyncio
@pytest.mark.parametrize(("fitted", "runs"), [("build|x|load|-", 1), (None, 0)])
async def test_the_nightly_run_fits_the_days_once_after_their_week_moved(
    monkeypatch, fake_goals, fitted, runs
):
    # Every day hard: the week departs from its targets, moved or not.
    await _nightly_user(
        [_session(JUNE + timedelta(days=d), "intervals") for d in range(14)], fitted
    )
    adapt_calls = 0

    async def adapt_training_plan(*args, **kwargs):
        nonlocal adapt_calls
        adapt_calls += 1
        return []

    async def no_weather(*args, **kwargs):
        return ""

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", adapt_training_plan)
    monkeypatch.setattr(plan_maintenance, "training_weather_context_for_user", no_weather)

    for _night in range(2):
        await plan_maintenance.run_daily_plan_maintenance(
            TestSessionLocal,
            now=datetime(2026, 6, 8, 2, 5, tzinfo=timezone.utc),
            timezone_name="UTC",
        )

    # A departure alone starts nothing: a writer may keep one on purpose (#666).
    assert adapt_calls == runs


@pytest.mark.asyncio
async def test_a_failed_nightly_run_leaves_the_move_for_the_next_night(monkeypatch, fake_goals):
    """The mark is in the same transaction as the run, so a failure rolls it back."""
    await _nightly_user(
        [_session(JUNE + timedelta(days=d), "intervals") for d in range(14)], "build|x|load|-"
    )
    outcomes = iter([RuntimeError("provider down"), [], []])
    adapt_calls = 0

    async def adapt_training_plan(*args, **kwargs):
        nonlocal adapt_calls
        adapt_calls += 1
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def no_weather(*args, **kwargs):
        return ""

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", adapt_training_plan)
    monkeypatch.setattr(plan_maintenance, "training_weather_context_for_user", no_weather)

    results = [
        await plan_maintenance.run_daily_plan_maintenance(
            TestSessionLocal,
            now=datetime(2026, 6, 8, 2, 5, tzinfo=timezone.utc),
            timezone_name="UTC",
        )
        for _night in range(3)
    ]

    assert results[0].failed == 1
    assert adapt_calls == 2


@pytest.mark.asyncio
async def test_a_week_the_model_left_out_is_asked_for_again(
    client, auth_headers, monkeypatch
):
    calls: list[list[str]] = []

    async def first_week_only(weeks, profile, **kwargs):
        calls.append([w["weekStart"] for w in weeks])
        return {weeks[0]["weekStart"]: "Only this one."}

    monkeypatch.setattr(ai_service, "write_week_goals", first_week_only)

    first = (await client.post("/api/v1/ai/season-plan/goals", headers=auth_headers)).json()
    await client.post("/api/v1/ai/season-plan/goals", headers=auth_headers)

    assert [w["goal"] for w in first["weeks"]].count("Only this one.") == 1
    assert [len(c) for c in calls] == [sp.SEASON_WEEKS, sp.SEASON_WEEKS - 1]
