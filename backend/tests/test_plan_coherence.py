"""A plan change must be checked against the days around it (#659).

Production, 2026-09-07: the nightly job had set Tuesday to a 45-minute "Core and
Upper Body Strength" session. In chat the athlete said they were tired and
thinking about the gym, so the coach moved *today* to the identical session —
same type, same title, same duration — and the plan then held that session twice
in a row. `plan_day_history` shows `source=coach_chat, applied=t`: the write
worked exactly as designed. Forty-four seconds later the athlete asked the coach
to review the coming days; it read the duplicate, described it correctly and
endorsed it.
"""

from services.plan_coherence import (
    DEFAULT_HORIZON_DAYS,
    find_repeated_sessions,
    session_signature,
)
from services.prompts import (
    ask_trainer_plan_updates_rule,
    ask_trainer_system_sections,
    plan_coherence_section,
)

MONDAY = "2026-09-07"
TUESDAY = "2026-09-08"
WEDNESDAY = "2026-09-09"


def _strength(date: str, **overrides) -> dict:
    day = {
        "date": date,
        "workoutType": "strength",
        "title": "Core and Upper Body Strength",
        "durationMinutes": 45,
    }
    day.update(overrides)
    return day


def _sections(**overrides):
    kwargs = {
        "profile": {"name": "Test"},
        "today": MONDAY,
        "last_7_days": [],
        "next_n_days": [],
        "assessment_section": "",
        "memory_section": "",
        "workout_section": "",
        "plan_updates_rule": "",
    }
    kwargs.update(overrides)
    return ask_trainer_system_sections(**kwargs)


# --- the production case ------------------------------------------------------


def test_the_2026_09_07_duplicate_is_detected():
    repeats = find_repeated_sessions([_strength(MONDAY), _strength(TUESDAY)], MONDAY)

    assert len(repeats) == 1
    assert repeats[0]["dates"] == [MONDAY, TUESDAY]
    assert repeats[0]["title"] == "Core and Upper Body Strength"
    assert repeats[0]["durationMinutes"] == 45


def test_the_duplicate_reaches_the_coach_as_a_stated_fact():
    warnings = plan_coherence_section(
        find_repeated_sessions([_strength(MONDAY), _strength(TUESDAY)], MONDAY), MONDAY
    )
    section = _sections(plan_coherence_warnings=warnings)["plan-coherence"]

    assert MONDAY in section and TUESDAY in section
    assert "SAME session" in section
    # Weekday anchoring, per the convention every plan-bearing prompt follows
    # (#462/#464/#625) — a bare ISO date is what started this whole class of bug.
    assert "Monday" in section and "Tuesday" in section


def test_the_coach_is_told_to_act_rather_than_merely_report():
    # Reading the plan was never the failure. It read it correctly and approved.
    warnings = plan_coherence_section(
        find_repeated_sessions([_strength(MONDAY), _strength(TUESDAY)], MONDAY), MONDAY
    )

    assert "Raise this yourself rather than waiting to be asked" in warnings
    assert "never describe the pair as a deliberate progression" in warnings


# --- what must NOT be flagged -------------------------------------------------


def test_a_clean_week_produces_nothing():
    plan = [
        _strength(MONDAY),
        {
            "date": TUESDAY,
            "workoutType": "endurance",
            "title": "Steady Aerobic Base Ride",
            "durationMinutes": 90,
        },
    ]

    assert find_repeated_sessions(plan, MONDAY) == []
    assert plan_coherence_section([], MONDAY) == ""


def test_consecutive_rest_days_are_not_a_collision():
    # A taper is repeated rest on purpose.
    plan = [
        {"date": MONDAY, "workoutType": "rest", "title": "Complete Rest Day",
         "durationMinutes": 0},
        {"date": TUESDAY, "workoutType": "rest", "title": "Complete Rest Day",
         "durationMinutes": 0},
    ]

    assert find_repeated_sessions(plan, MONDAY) == []


def test_the_same_session_a_week_apart_is_not_a_collision():
    # Weekly repetition is what a training plan *is*. Only adjacency is the smell.
    plan = [_strength(MONDAY), _strength("2026-09-14")]

    assert find_repeated_sessions(plan, MONDAY) == []


def test_two_sessions_on_one_day_are_not_compared_with_each_other():
    # A two-a-day is deliberate; identity is (date, slot) and both live on the
    # same date, so there is no adjacency to complain about (#496).
    plan = [_strength(MONDAY, slot=0), _strength(MONDAY, slot=1)]

    assert find_repeated_sessions(plan, MONDAY) == []


def test_a_repeat_across_a_two_a_day_is_still_caught():
    # Slot must not hide the collision: the session repeats on the next day.
    plan = [
        _strength(MONDAY, slot=1),
        {"date": MONDAY, "workoutType": "endurance", "title": "Easy Spin",
         "durationMinutes": 60, "slot": 0},
        _strength(TUESDAY, slot=0),
    ]

    repeats = find_repeated_sessions(plan, MONDAY)

    assert [r["dates"] for r in repeats] == [[MONDAY, TUESDAY]]


def test_differing_duration_is_a_different_session():
    plan = [_strength(MONDAY), _strength(TUESDAY, durationMinutes=90)]

    assert find_repeated_sessions(plan, MONDAY) == []


def test_a_day_without_a_title_is_never_a_duplicate():
    # Absent data is not evidence of a collision; guessing one would invent it.
    plan = [_strength(MONDAY, title=""), _strength(TUESDAY, title="")]

    assert find_repeated_sessions(plan, MONDAY) == []
    assert session_signature(_strength(MONDAY, title="")) is None


def test_zero_duration_days_carry_no_load():
    assert session_signature(_strength(MONDAY, durationMinutes=0)) is None


# --- windowing ----------------------------------------------------------------


def test_a_duplicate_already_in_the_past_is_not_reported():
    # The athlete has ridden it. There is nothing the coach can do about it, and
    # raising it would just be noise on every message.
    plan = [_strength("2026-09-01"), _strength("2026-09-02")]

    assert find_repeated_sessions(plan, MONDAY) == []


def test_a_duplicate_starting_today_is_reported():
    plan = [_strength(MONDAY), _strength(TUESDAY)]

    assert len(find_repeated_sessions(plan, MONDAY)) == 1


def test_a_duplicate_beyond_the_horizon_is_not_reported():
    far = "2026-10-20"
    plan = [_strength(far), _strength("2026-10-21")]

    assert find_repeated_sessions(plan, MONDAY) == []
    assert DEFAULT_HORIZON_DAYS == 14


def test_without_a_today_the_whole_plan_is_examined():
    plan = [_strength("2026-09-01"), _strength("2026-09-02")]

    assert len(find_repeated_sessions(plan, None)) == 1


def test_three_identical_days_report_both_adjacent_pairs():
    plan = [_strength(MONDAY), _strength(TUESDAY), _strength(WEDNESDAY)]

    repeats = find_repeated_sessions(plan, MONDAY)

    assert [r["dates"] for r in repeats] == [[MONDAY, TUESDAY], [TUESDAY, WEDNESDAY]]


# --- robustness ---------------------------------------------------------------


def test_garbage_days_do_not_raise():
    plan = [None, "nonsense", {}, {"date": "not-a-date"}, _strength(MONDAY)]

    assert find_repeated_sessions(plan, MONDAY) == []


def test_an_empty_plan_is_fine():
    assert find_repeated_sessions(None, MONDAY) == []
    assert find_repeated_sessions([], MONDAY) == []


# --- the write-side rule ------------------------------------------------------


def test_the_plan_updates_rule_requires_checking_the_adjacent_days():
    for rule in (
        ask_trainer_plan_updates_rule(None),
        ask_trainer_plan_updates_rule({"date": MONDAY, "title": "Recovery"}),
    ):
        assert "the day before and the day after" in rule
        assert "same planUpdates array" in rule.replace("SAME", "same")


def test_the_rule_covers_the_substitution_case_that_caused_this():
    # "You were going to the gym anyway" is what the coach itself said. Its own
    # reasoning implied Tuesday's identical session had become redundant.
    rule = ask_trainer_plan_updates_rule(None)

    assert "already scheduled later that week" in rule
    assert "redundant" in rule


def test_a_clean_plan_adds_no_section_to_the_prompt():
    # The prompt is ~16k tokens already (#556); this section costs nothing when
    # there is nothing to say.
    assert _sections()["plan-coherence"] == ""
