"""Concurrent-training interference, enforced at the one plan gate (#715).

#665 put the coherence check at `_enforce_and_persist` and drew a line it could
not cross: "whether a strength day may sit next to a threshold session depends on
whether it loads the legs — real coaching, stated in the prompt and left there."

#714 moved that line by giving a planned gym session its prescribed lifts, so
"does it load the legs" is now a list of exercises with an intensity unit rather
than a judgement. These tests pin both halves: what the detector decides (and
what it deliberately refuses to decide), and what the gate does about it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
import models
from auth import hash_password
from services import interference, plan_pipeline
from services.dates import app_today
from services.prompts import plan_interference_section
from tests.conftest import TestSessionLocal


def _day(
    date: str,
    workout_type: str = "endurance",
    title: str | None = None,
    duration: int = 60,
    **extra,
) -> dict:
    return {
        "date": date,
        "workoutType": workout_type,
        "title": title or f"Workout {date}",
        "description": "Session",
        "durationMinutes": duration,
        **extra,
    }


def _gym(
    date: str,
    *,
    lifts: list[dict] | None = None,
    title: str = "Lower Body",
    duration: int = 60,
    **extra,
) -> dict:
    """A planned gym session, heavy on the legs unless told otherwise."""
    return _day(
        date,
        "strength",
        title,
        duration,
        sport="strength",
        strengthExercises=(
            lifts
            if lifts is not None
            else [{"exercise": "back squat", "sets": 4, "reps": 5, "rir": 2}]
        ),
        **extra,
    )


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


def _dates(count: int = 5, offset: int = 1) -> list[str]:
    return [(app_today() + timedelta(days=offset + i)).isoformat() for i in range(count)]


def _today() -> str:
    return app_today().isoformat()


async def _commit(user_id: str, plan: list[dict], source: str):
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = await crud.get_training_plan(db, user_id)
        base = current.plan if current is not None else []
        result = await plan_pipeline.commit_plan(
            db, user, plan, base_plan=base, source=source
        )
        await db.commit()
        return result


def _sessions(plan: list[dict]) -> dict[tuple[str, int], dict]:
    from schemas import session_key

    return {session_key(d): d for d in plan}


async def _history(user_id: str, date: str) -> list[models.PlanDayHistory]:
    async with TestSessionLocal() as db:
        return list(await crud.list_plan_day_history(db, user_id, date=date))


# ---------------------------------------------------------------------------
# What counts as heavy lower-body work
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "back squat",
        "Barbell Back Squat",
        "front squat",
        "bulgarian split squat",
        "deadlift",
        "romanian deadlift",
        "rdl",
        "good morning",
        "walking lunge",
        "leg press",
        "step up",
        "hip thrust",
        "trap bar deadlift",
    ],
)
def test_the_compound_lower_body_lifts_are_recognised(name):
    assert interference.is_lower_body_exercise(name)


@pytest.mark.parametrize(
    "name",
    ["bench press", "pull up", "calf raise", "leg extension", "leg curl", "plank", "", None],
)
def test_isolation_and_upper_body_work_is_not(name):
    """Isolation work is excluded on purpose, not by omission.

    A heavy set of calf raises is not what costs an athlete tomorrow's interval
    session, and a guard that moved the plan for one would be wrong far more
    often than right.
    """
    assert not interference.is_lower_body_exercise(name)


def test_a_prescription_that_names_no_intensity_is_not_read_as_heavy():
    """"3x8 squat" did not say how hard. Guessing would revert writes on nothing."""
    day = _gym(_dates(1)[0], lifts=[{"exercise": "back squat", "sets": 3, "reps": 8}])
    assert interference.heavy_lower_body_lifts(day) == []


@pytest.mark.parametrize(
    "lift,heavy",
    [
        ({"exercise": "back squat", "rir": 2}, True),
        ({"exercise": "back squat", "rir": 3}, True),
        ({"exercise": "back squat", "rir": 4}, False),
        ({"exercise": "back squat", "percentE1rm": 80.0}, True),
        ({"exercise": "back squat", "percentE1rm": 75.0}, True),
        ({"exercise": "back squat", "percent_e1rm": 80.0}, True),
        ({"exercise": "back squat", "percentE1rm": 60.0}, False),
        ({"exercise": "bench press", "rir": 0}, False),
        # ``True`` is an int in Python and would otherwise read as RIR 1.
        ({"exercise": "back squat", "rir": True}, False),
        ({"exercise": "back squat", "percentE1rm": True}, False),
        ({"exercise": "back squat", "rir": "two"}, False),
    ],
)
def test_the_intensity_unit_decides(lift, heavy):
    day = _gym(_dates(1)[0], lifts=[lift])
    assert bool(interference.heavy_lower_body_lifts(day)) is heavy


def test_an_unreadable_entry_in_the_list_does_not_hide_the_readable_ones():
    day = _gym(
        _dates(1)[0],
        lifts=["3x5 squat", None, {"exercise": "deadlift", "rir": 1}],
    )
    assert interference.heavy_lower_body_lifts(day) == ["deadlift"]


def test_both_heavy_lifts_are_named_in_the_statement():
    mon, tue = _dates(2)
    finding = interference.find_heavy_legs_before_key(
        [
            _gym(
                mon,
                lifts=[
                    {"exercise": "back squat", "rir": 2},
                    {"exercise": "romanian deadlift", "rir": 2},
                ],
            ),
            _day(tue, "intervals"),
        ],
        _today(),
    )[0]
    statement = interference.finding_statement(finding)
    assert "back squat and romanian deadlift" in statement


@pytest.mark.parametrize("junk", [None, 42, "a day", [], True])
def test_a_malformed_plan_day_cannot_raise_out_of_the_detector(junk):
    """Every predicate here reads LLM and legacy data, so none of them may raise."""
    assert interference.heavy_lower_body_lifts(junk) == []
    assert interference.is_strength_session(junk) is False
    assert interference.is_key_endurance_session(junk) is False


def test_a_plan_full_of_junk_simply_has_no_findings():
    plan = [None, 42, {"date": "not-a-date"}, {"workoutType": "intervals"}]
    assert interference.find_interference(plan, _today()) == []


def test_two_gym_sessions_on_one_date_are_not_an_interference_pair():
    """Stacked strength is ``plan_coherence``'s finding, not this module's."""
    thu = _dates(1)[0]
    assert (
        interference.find_insufficient_separation(
            [
                _gym(thu, slot=0, title="Upper Body", timeOfDay="07:00"),
                _gym(thu, slot=1, title="Lower Body", timeOfDay="09:00"),
            ],
            _today(),
        )
        == []
    )


def test_a_session_that_is_not_a_gym_session_has_no_lifts_to_read():
    """Even if something put a prescription on a ride."""
    day = _day(
        _dates(1)[0],
        "intervals",
        strengthExercises=[{"exercise": "back squat", "rir": 1}],
    )
    assert interference.heavy_lower_body_lifts(day) == []


@pytest.mark.parametrize("given", [None, 42, "squats", {"exercise": "back squat"}])
def test_an_unreadable_prescription_list_is_simply_absent(given):
    day = _gym(_dates(1)[0])
    day["strengthExercises"] = given
    assert interference.heavy_lower_body_lifts(day) == []


# ---------------------------------------------------------------------------
# What counts as a key session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workout_type", ["intervals", "race"])
def test_the_quality_session_types_are_key(workout_type):
    assert interference.is_key_endurance_session(_day(_dates(1)[0], workout_type))


@pytest.mark.parametrize(
    "workout_type", ["endurance", "tempo", "recovery", "rest", "strength", ""]
)
def test_everything_else_is_not(workout_type):
    """``tempo`` is the deliberate exclusion.

    A tempo ride still does its job on tired legs, so lifting Tuesday and riding
    tempo Wednesday is a normal week rather than an error — and a guard firing on
    it would be correcting a competent coach.
    """
    assert not interference.is_key_endurance_session(_day(_dates(1)[0], workout_type))


def test_a_gym_session_is_never_a_key_endurance_session():
    assert not interference.is_key_endurance_session(
        _day(_dates(1)[0], "intervals", sport="strength")
    )


def test_the_sport_field_decides_a_run_is_a_run():
    run = _day(_dates(1)[0], "intervals", sport="running")
    assert interference.is_key_endurance_session(run)
    assert interference.find_heavy_legs_before_key(
        [_gym(_today()), run], _today()
    )[0]["sport"] == "running"


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


def test_heavy_legs_the_day_before_the_intervals_is_found():
    mon, tue = _dates(2)
    findings = interference.find_heavy_legs_before_key(
        [_gym(mon), _day(tue, "intervals", "4x8 min Threshold", 75)], _today()
    )
    assert [f["rule"] for f in findings] == [
        interference.RULE_HEAVY_LEGS_BEFORE_KEY
    ]
    assert findings[0]["lifts"] == ["back squat"]
    assert findings[0]["strength_date"] == mon
    assert findings[0]["key_date"] == tue


def test_two_days_before_the_intervals_is_a_normal_week():
    """Reaching further back would start rejecting weeks that are fine."""
    mon, _tue, wed = _dates(3)
    assert (
        interference.find_heavy_legs_before_key(
            [_gym(mon), _day(wed, "intervals")], _today()
        )
        == []
    )


def test_the_gym_session_is_not_compared_with_yesterday():
    """Only the day *before* a key session, not the day after it."""
    mon, tue = _dates(2)
    assert (
        interference.find_heavy_legs_before_key(
            [_day(mon, "intervals"), _gym(tue)], _today()
        )
        == []
    )


def test_a_clash_the_athlete_has_already_ridden_past_is_history():
    yesterday = (app_today() - timedelta(days=1)).isoformat()
    assert (
        interference.find_interference(
            [_gym(yesterday), _day(_today(), "intervals")], _today()
        )
        == []
    )


def test_a_clash_beyond_the_horizon_is_not_reported():
    far = _dates(2, offset=40)
    assert (
        interference.find_interference(
            [_gym(far[0]), _day(far[1], "intervals")], _today()
        )
        == []
    )


def test_lifting_ahead_of_the_same_days_intervals_is_found():
    thu = _dates(1)[0]
    findings = interference.find_strength_before_intensity(
        [_gym(thu, slot=0), _day(thu, "intervals", slot=1)], _today()
    )
    assert [f["rule"] for f in findings] == [
        interference.RULE_STRENGTH_BEFORE_INTENSITY
    ]
    assert (findings[0]["strength_slot"], findings[0]["key_slot"]) == (0, 1)


def test_lifting_after_the_same_days_intervals_is_the_right_order():
    thu = _dates(1)[0]
    assert (
        interference.find_strength_before_intensity(
            [_day(thu, "intervals", slot=0), _gym(thu, slot=1)], _today()
        )
        == []
    )


def test_the_ordering_rule_needs_no_clock_at_all():
    """It reads ``slot``, which is the session's order within the date (#496)."""
    thu = _dates(1)[0]
    plan = [_gym(thu, slot=0), _day(thu, "intervals", slot=1)]
    assert all(d.get("timeOfDay") is None for d in plan)
    assert interference.find_strength_before_intensity(plan, _today())


@pytest.mark.parametrize(
    "raw,minutes",
    [
        ("18:30", 1110),
        ("6pm", 1080),
        ("6 pm", 1080),
        ("06.00", 360),
        ("18h30", 1110),
        ("12am", 0),
        ("12pm", 720),
        # A bare hour past noon can only be 24-hour, so it is read.
        ("18", 1080),
        ("12", 720),
        ("25:00", None),
        ("10:70", None),
        ("morning", None),
        ("", None),
        (None, None),
        (True, None),
    ],
)
def test_only_an_actual_clock_time_reads_as_one(raw, minutes):
    assert interference.minutes_into_day(raw) == minutes


@pytest.mark.parametrize("raw", ["6", "7", "9", "11"])
def test_a_bare_morning_hour_is_ambiguous_and_is_refused(raw):
    """"6" is as likely the evening as the morning.

    Reading it as 06:00 would turn a four-hour gap into a twelve-hour one and
    quietly clear the separation guard. Writing the minutes states the 24-hour
    convention and is read.
    """
    assert interference.minutes_into_day(raw) is None
    assert interference.minutes_into_day(f"{raw}:00") == int(raw) * 60


def test_a_ride_three_hours_before_the_gym_is_too_close():
    thu = _dates(1)[0]
    findings = interference.find_insufficient_separation(
        [
            _day(thu, "endurance", slot=0, timeOfDay="16:00"),
            _gym(thu, slot=1, timeOfDay="19:00"),
        ],
        _today(),
    )
    assert [f["rule"] for f in findings] == [
        interference.RULE_INSUFFICIENT_SEPARATION
    ]
    assert findings[0]["hours_apart"] == 3.0


def test_a_morning_ride_and_an_evening_gym_session_are_far_enough_apart():
    thu = _dates(1)[0]
    assert (
        interference.find_insufficient_separation(
            [
                _day(thu, "endurance", slot=0, timeOfDay="06:00"),
                _gym(thu, slot=1, timeOfDay="19:00"),
            ],
            _today(),
        )
        == []
    )


def test_words_are_not_hours():
    """"morning" and "evening" state no separation, so none is claimed either way.

    Converting them to notional clock times would manufacture a nine-hour gap
    the athlete never wrote, and then clear the guard on a number nobody stated.
    """
    thu = _dates(1)[0]
    assert (
        interference.find_insufficient_separation(
            [
                _day(thu, "endurance", slot=0, timeOfDay="morning"),
                _gym(thu, slot=1, timeOfDay="evening"),
            ],
            _today(),
        )
        == []
    )


def test_a_rest_day_is_not_an_endurance_session_to_be_separated_from():
    thu = _dates(1)[0]
    assert (
        interference.find_insufficient_separation(
            [
                _day(thu, "rest", slot=0, timeOfDay="16:00"),
                _gym(thu, slot=1, timeOfDay="19:00"),
            ],
            _today(),
        )
        == []
    )


def test_a_coherent_week_produces_nothing():
    mon, tue, wed = _dates(3)
    assert (
        interference.find_interference(
            [
                _day(mon, "intervals"),
                _gym(tue, lifts=[{"exercise": "bench press", "rir": 1}]),
                _day(wed, "endurance"),
            ],
            _today(),
        )
        == []
    )


# ---------------------------------------------------------------------------
# The weighting
# ---------------------------------------------------------------------------


def test_running_interferes_more_than_cycling():
    mon, tue = _dates(2)
    ride = interference.find_heavy_legs_before_key(
        [_gym(mon), _day(tue, "intervals")], _today()
    )[0]
    run = interference.find_heavy_legs_before_key(
        [_gym(mon), _day(tue, "intervals", sport="running")], _today()
    )[0]
    assert run["weight"] > ride["weight"]


def test_endurance_before_strength_outweighs_strength_before_endurance():
    """The direction the interference literature says costs more."""
    thu = _dates(1)[0]
    endurance_first = interference.find_insufficient_separation(
        [
            _day(thu, "endurance", slot=0, timeOfDay="16:00"),
            _gym(thu, slot=1, timeOfDay="19:00"),
        ],
        _today(),
    )[0]
    strength_first = interference.find_strength_before_intensity(
        [_gym(thu, slot=0), _day(thu, "intervals", slot=1)], _today()
    )[0]
    assert endurance_first["sport"] == strength_first["sport"] == "cycling"
    assert endurance_first["weight"] > strength_first["weight"]


def test_an_unreadable_sport_weighs_as_cycling_rather_than_the_worst_case():
    assert interference.interference_weight({"rule": "x", "sport": "kayaking"}) == (
        interference.interference_weight({"rule": "x", "sport": "cycling"})
    )


def test_the_heaviest_finding_is_stated_first():
    mon, tue = _dates(2)
    findings = interference.find_interference(
        [
            _gym(mon),
            _day(tue, "intervals", sport="running", slot=0, timeOfDay="17:00"),
            _gym(tue, slot=1, timeOfDay="19:00"),
        ],
        _today(),
    )
    assert [f["weight"] for f in findings] == sorted(
        (f["weight"] for f in findings), reverse=True
    )


def test_the_finding_key_is_structural_not_about_which_lift():
    """A pre-existing clash stays the same clash when the heavy lift changes.

    Deliberately unlike ``plan_coherence.collision_keys``, which carries the
    session signature: there, two days holding a *different* duplicate session
    genuinely is a new collision, because the duplication is the finding.
    """
    mon, tue = _dates(2)
    squat = interference.interference_keys(
        [_gym(mon), _day(tue, "intervals")], _today()
    )
    deadlift = interference.interference_keys(
        [
            _gym(mon, lifts=[{"exercise": "deadlift", "rir": 1}]),
            _day(tue, "intervals"),
        ],
        _today(),
    )
    assert set(squat) == set(deadlift)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heavy_squats_the_day_before_the_intervals_are_reverted():
    """The acceptance criterion of #715, as a plan write."""
    tue, wed = _dates(2)
    user_id = await _create_user(
        "interference-legs@example.com",
        [
            _day(tue, "endurance", "Steady Ride", 90),
            _day(wed, "intervals", "4x8 min Threshold", 75),
        ],
    )

    result = await _commit(
        user_id,
        [_gym(tue), _day(wed, "intervals", "4x8 min Threshold", 75)],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(tue, 0)]["title"] == "Steady Ride"
    assert sessions[(wed, 0)]["title"] == "4x8 min Threshold"


@pytest.mark.asyncio
async def test_the_revert_records_the_rule_it_applied():
    """A correction the athlete cannot account for teaches them nothing."""
    tue, wed = _dates(2)
    user_id = await _create_user(
        "interference-reason@example.com",
        [
            _day(tue, "endurance", "Steady Ride", 90),
            _day(wed, "intervals", "4x8 min Threshold", 75),
        ],
    )

    await _commit(
        user_id,
        [_gym(tue), _day(wed, "intervals", "4x8 min Threshold", 75)],
        "nightly_maintenance",
    )

    rows = await _history(user_id, tue)
    blocked = [r for r in rows if not r.applied]
    assert blocked, "the reverted session must leave an applied=False record"
    reasons = [r.reason for r in rows if r.reason]
    assert reasons, "the correction must be attributable in plan_day_history"
    assert "back squat" in reasons[0]
    assert "intervals" in reasons[0]
    assert "gym session was reverted" in reasons[0]


@pytest.mark.asyncio
async def test_the_key_session_is_reverted_when_the_gym_day_is_not_this_writes():
    """The gym day predates the write, so there is nothing to give back there.

    The clash is still one this write created, so it is still undone — on the
    only side this write is entitled to touch.
    """
    tue, wed = _dates(2)
    user_id = await _create_user(
        "interference-otherside@example.com",
        [_gym(tue), _day(wed, "endurance", "Steady Ride", 90)],
    )

    result = await _commit(
        user_id,
        [_gym(tue), _day(wed, "intervals", "4x8 min Threshold", 75)],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(wed, 0)]["title"] == "Steady Ride"
    rows = await _history(user_id, wed)
    reasons = [r.reason for r in rows if r.reason]
    assert reasons and "did not change the gym session" in reasons[0]


@pytest.mark.asyncio
async def test_a_newly_appended_gym_day_says_so_rather_than_claiming_it_is_old():
    """The same revert, for a different reason, so it must give that reason.

    One wording for both cases wrote a false sentence into the history whenever
    the gym session was the one this write had just appended.
    """
    tue, wed = _dates(2)
    user_id = await _create_user(
        "interference-newgym@example.com",
        [_day(wed, "endurance", "Steady Ride", 90)],
    )

    result = await _commit(
        user_id,
        [_gym(tue), _day(wed, "intervals", "4x8 min Threshold", 75)],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(tue, 0)]["workoutType"] == "strength", "the appended day stays"
    assert sessions[(wed, 0)]["title"] == "Steady Ride"
    reasons = [r.reason for r in await _history(user_id, wed) if r.reason]
    assert reasons and "has no earlier version to restore" in reasons[0]
    assert "predates" not in reasons[0]


@pytest.mark.asyncio
async def test_a_pre_existing_clash_is_not_unwound_by_an_unrelated_write():
    """Undoing an unrelated edit over someone else's mess would punish the write."""
    tue, wed, thu = _dates(3)
    user_id = await _create_user(
        "interference-preexisting@example.com",
        [
            _gym(tue),
            _day(wed, "intervals", "4x8 min Threshold", 75),
            _day(thu, "endurance", "Steady Ride", 90),
        ],
    )

    result = await _commit(
        user_id,
        [
            _gym(tue),
            _day(wed, "intervals", "4x8 min Threshold", 75),
            _day(thu, "recovery", "Easy Spin", 45),
        ],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(thu, 0)]["title"] == "Easy Spin"
    assert sessions[(tue, 0)]["workoutType"] == "strength"
    assert sessions[(wed, 0)]["title"] == "4x8 min Threshold"


@pytest.mark.asyncio
async def test_a_clash_created_by_appending_is_left_alone_and_reported():
    """Deleting an appended session to resolve a clash is the #651 mistake."""
    tue, wed = _dates(2)
    user_id = await _create_user("interference-appended@example.com", [])

    result = await _commit(
        user_id,
        [_gym(tue), _day(wed, "intervals", "4x8 min Threshold", 75)],
        "generate",
    )

    sessions = _sessions(result.plan)
    assert sessions[(tue, 0)]["workoutType"] == "strength"
    assert sessions[(wed, 0)]["title"] == "4x8 min Threshold"
    # Nothing was corrected, so the coach is the one who has to act on it.
    assert plan_interference_section(
        interference.find_interference(result.plan, _today()), _today()
    )


@pytest.mark.asyncio
async def test_a_clash_appended_beside_an_existing_week_is_also_left_alone():
    """Neither session has a previous version, so there is nothing to give back.

    Distinct from the empty-plan case above: here the athlete *has* a week, and
    the guard still has to recognise that this particular clash is made of two
    sessions it cannot revert without deleting them.
    """
    mon = _dates(1)[0]
    thu, fri = _dates(2, offset=4)
    user_id = await _create_user(
        "interference-appended-beside@example.com",
        [_day(mon, "endurance", "Steady Ride", 90)],
    )

    result = await _commit(
        user_id,
        [
            _day(mon, "endurance", "Steady Ride", 90),
            _gym(thu),
            _day(fri, "intervals", "4x8 min Threshold", 75),
        ],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(thu, 0)]["workoutType"] == "strength"
    assert sessions[(fri, 0)]["title"] == "4x8 min Threshold"


@pytest.mark.asyncio
async def test_the_athlete_asking_for_it_still_gets_it():
    """Guiding automated writers, not blocking the athlete."""
    tue, wed = _dates(2)
    user_id = await _create_user(
        "interference-coach@example.com",
        [
            _day(tue, "endurance", "Steady Ride", 90),
            _day(wed, "intervals", "4x8 min Threshold", 75),
        ],
    )

    result = await _commit(
        user_id,
        [_gym(tue), _day(wed, "intervals", "4x8 min Threshold", 75)],
        "coach_chat",
    )

    assert _sessions(result.plan)[(tue, 0)]["workoutType"] == "strength"


@pytest.mark.asyncio
async def test_lifting_moved_ahead_of_the_intervals_is_put_back():
    """Same-date ordering: the quality session keeps its place, the gym work does not.

    The write wanted the intervals *and* wanted to lift first. It keeps the
    intervals; the gym session is the companion load and is the side given back.
    """
    thu = _dates(1)[0]
    user_id = await _create_user(
        "interference-order@example.com",
        [
            _day(thu, "endurance", "Morning Spin", 60, slot=0),
            _day(thu, "endurance", "Evening Spin", 45, slot=1),
        ],
    )

    result = await _commit(
        user_id,
        [
            _gym(thu, slot=0),
            _day(thu, "intervals", "VO2 Reps", 60, slot=1),
        ],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(thu, 0)]["title"] == "Morning Spin"
    assert sessions[(thu, 1)]["title"] == "VO2 Reps"


@pytest.mark.asyncio
async def test_a_same_day_swap_blocked_at_one_end_is_undone_at_both():
    """Swapping the two sessions of a date is one decision, so it reverts as one.

    Here the write does not merely add a gym session ahead of the intervals — it
    *moves* the existing gym session from the evening slot to the morning one.
    Reverting only the morning slot would leave the gym session on neither, which
    is the #651 failure mode, so the move repair gives the evening slot back too
    and the athlete keeps the day they had.
    """
    thu = _dates(1)[0]
    user_id = await _create_user(
        "interference-swap@example.com",
        [
            _day(thu, "endurance", "Morning Spin", 60, slot=0),
            _gym(thu, slot=1),
        ],
    )

    result = await _commit(
        user_id,
        [
            _gym(thu, slot=0),
            _day(thu, "intervals", "VO2 Reps", 60, slot=1),
        ],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(thu, 0)]["title"] == "Morning Spin"
    assert sessions[(thu, 1)]["title"] == "Lower Body"


@pytest.mark.asyncio
async def test_a_ride_moved_too_close_to_the_gym_session_is_put_back():
    thu = _dates(1)[0]
    user_id = await _create_user(
        "interference-separation@example.com",
        [
            _day(thu, "endurance", "Morning Spin", 60, slot=0, timeOfDay="06:00"),
            _gym(thu, slot=1, timeOfDay="19:00"),
        ],
    )

    result = await _commit(
        user_id,
        [
            _day(thu, "endurance", "Afternoon Spin", 60, slot=0, timeOfDay="17:00"),
            _gym(thu, slot=1, timeOfDay="19:00"),
        ],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(thu, 0)]["timeOfDay"] == "06:00"
    rows = await _history(user_id, thu)
    reasons = [r.reason for r in rows if r.reason]
    assert reasons and "2.0 h apart" in reasons[0]


@pytest.mark.asyncio
async def test_a_correct_week_passes_through_untouched():
    """The guard must be invisible to a write that was fine."""
    tue, wed = _dates(2)
    user_id = await _create_user(
        "interference-clean@example.com",
        [
            _day(tue, "endurance", "Steady Ride", 90),
            _day(wed, "intervals", "4x8 min Threshold", 75),
        ],
    )

    result = await _commit(
        user_id,
        [
            _day(tue, "recovery", "Easy Spin", 45),
            _day(wed, "intervals", "4x8 min Threshold", 75),
        ],
        "nightly_maintenance",
    )

    assert _sessions(result.plan)[(tue, 0)]["title"] == "Easy Spin"


@pytest.mark.asyncio
async def test_a_blocked_move_does_not_leave_the_session_on_neither_day():
    """The #651 failure mode must not reappear through a guard that runs late.

    The write moves the gym session onto the day before the intervals. The
    destination is reverted — and the source had already been emptied, so without
    the move repair running *after* this guard the session would exist nowhere.
    """
    mon, tue, wed = _dates(3)
    user_id = await _create_user(
        "interference-move@example.com",
        [
            _gym(mon, title="Lower Body"),
            _day(tue, "rest", "Rest", 0),
            _day(wed, "intervals", "4x8 min Threshold", 75),
        ],
    )

    result = await _commit(
        user_id,
        [
            _day(mon, "rest", "Rest", 0),
            _gym(tue, title="Lower Body"),
            _day(wed, "intervals", "4x8 min Threshold", 75),
        ],
        "nightly_maintenance",
    )

    sessions = _sessions(result.plan)
    assert sessions[(mon, 0)]["title"] == "Lower Body", "the move's source was emptied"
    assert sessions[(tue, 0)]["workoutType"] == "rest"
    assert sessions[(wed, 0)]["title"] == "4x8 min Threshold"


# ---------------------------------------------------------------------------
# The rationale reaches the athlete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_revert_is_never_narrated_as_a_change_to_the_plan():
    """Restoring stored content is not a change, and must not read as one.

    A guard revert leaves the plan exactly as the athlete last saw it, so there
    is nothing for the coach to announce — and announcing it would mean a chat
    message every night the nightly job retried the same bad combination. The
    rule reaches the athlete through the coach's prompt instead, where they are
    present to discuss it. What the history keeps is the attempt and its reason.
    """
    tue, wed = _dates(2)
    user_id = await _create_user(
        "interference-silent@example.com",
        [
            _day(tue, "endurance", "Steady Ride", 90),
            _day(wed, "intervals", "4x8 min Threshold", 75),
        ],
    )

    result = await _commit(
        user_id,
        [_gym(tue), _day(wed, "intervals", "4x8 min Threshold", 75)],
        "nightly_maintenance",
    )

    assert result.applied_changes == []
    rows = await _history(user_id, tue)
    assert [r.applied for r in rows] == [False]
    assert rows[0].reason


@pytest.mark.asyncio
async def test_the_narrator_does_not_overwrite_the_guards_recorded_rule():
    """The guard's reason is a fact; the retelling of it must not replace it."""
    tue = _dates(1)[0]
    user_id = await _create_user("interference-noclobber@example.com", [])
    async with TestSessionLocal() as db:
        rows = await crud.record_plan_day_changes(
            db,
            user_id,
            [{"date": tue, "slot": 0, "reason": "The rule the gate applied."}],
            "nightly_maintenance",
        )
        batch_id = rows[0].batch_id
        await crud.set_plan_day_reasons(
            db, batch_id, {tue: "Something the narrator made up."}
        )
        await db.commit()

    rows = await _history(user_id, tue)
    assert rows[0].reason == "The rule the gate applied."


@pytest.mark.asyncio
async def test_a_narrated_reason_still_lands_on_a_row_that_has_none():
    tue = _dates(1)[0]
    user_id = await _create_user("interference-narrated@example.com", [])
    async with TestSessionLocal() as db:
        rows = await crud.record_plan_day_changes(
            db, user_id, [{"date": tue, "slot": 0}], "nightly_maintenance"
        )
        await crud.set_plan_day_reasons(
            db, rows[0].batch_id, {tue: "Because you were tired."}
        )
        await db.commit()

    rows = await _history(user_id, tue)
    assert rows[0].reason == "Because you were tired."


@pytest.mark.asyncio
async def test_every_plan_writer_is_given_the_finding():
    """Shared with the coherence audit, in the one slot every writer already has.

    Both answer the same question — what is wrong with this week that is
    decidable without an LLM — so they share a prompt block rather than a second
    parameter threaded through four call sites. That is what #666 moved the
    construction here for.
    """
    from services import plan_context

    mon, tue, wed = _dates(3)
    user_id = await _create_user("interference-writer@example.com", [])
    plan = [
        _gym(mon),
        _day(tue, "intervals", "4x8 min Threshold", 75),
        # A repeat as well, so both audits have something to say.
        _day(wed, "intervals", "4x8 min Threshold", 75),
    ]
    async with TestSessionLocal() as db:
        context = await plan_context.plan_writer_context(db, user_id, plan)

    assert "Plan coherence check" in context.coherence
    assert "Concurrent-training check" in context.coherence
    assert "back squat" in context.coherence


@pytest.mark.asyncio
async def test_a_clean_week_costs_the_prompt_nothing():
    from services import plan_context

    mon, tue = _dates(2)
    user_id = await _create_user("interference-writer-clean@example.com", [])
    async with TestSessionLocal() as db:
        context = await plan_context.plan_writer_context(
            db, user_id, [_day(mon, "endurance"), _day(tue, "intervals")]
        )

    assert context.coherence == ""


def test_the_prompt_section_is_empty_for_a_week_with_nothing_wrong():
    assert plan_interference_section([], _today()) == ""
    assert plan_interference_section(None) == ""


def test_the_prompt_section_states_the_rule_and_the_direction():
    mon, tue = _dates(2)
    section = plan_interference_section(
        interference.find_interference(
            [_gym(mon), _day(tue, "intervals", "4x8 min Threshold", 75)], _today()
        ),
        _today(),
    )
    assert "treat as fact" in section
    assert "back squat" in section
    assert "running interferes more than cycling" in section
