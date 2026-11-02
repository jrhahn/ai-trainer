"""A plan update that makes a day another kind of session (ai-trainer-ops#338).

The model runs of the knee scenario: the coach turned "SFR 5x6 at 50 rpm" into
endurance with a new title and no description in 5 of 10 replies, and the merge
kept the SFR text under the easy day's title.
"""

import pytest

import schemas
from services import plan_pipeline

DATE = "2026-11-03"


def _validate(**fields) -> schemas.PlanDay:
    return schemas.PlanDay.model_validate(
        {"date": DATE, "durationMinutes": 75, **fields}
    )


def _update(**fields) -> schemas.PlanDayUpdateSchema:
    return schemas.PlanDayUpdateSchema(date=DATE, **fields)


def test_a_new_kind_of_session_leaves_the_old_one_s_text_and_structure():
    """Turned into endurance with a title only, the SFR text stayed under it in
    5 of 10 model runs (ai-trainer-ops#338)."""
    base = _validate(
        workoutType="tempo",
        title="SFR 5x6",
        description="SFR 5x6 at 50 rpm",
        workoutPurpose="Torque",
        keyFocusPoints=["Low cadence"],
        strengthExercises=[{"exercise": "squat", "sets": 3, "reps": 5}],
    )

    merged = schemas.merge_update(
        base, _update(workout_type="endurance", title="Z2, high cadence")
    )

    assert (merged.workout_type, merged.title, merged.description) == (
        "endurance",
        "Z2, high cadence",
        "Z2, high cadence",
    )
    assert merged.workout_purpose is None
    assert merged.key_focus_points is None
    assert merged.strength_exercises is None


def test_a_new_kind_of_session_keeps_what_the_update_restates():
    base = _validate(workoutType="intervals", title="VO2", description="5x4 hard")

    merged = schemas.merge_update(
        base,
        _update(
            workout_type="endurance", description="Easy spin", key_focus_points=["Spin"]
        ),
    )

    assert (merged.title, merged.description, merged.key_focus_points) == (
        "VO2",
        "Easy spin",
        ["Spin"],
    )


@pytest.mark.parametrize("workout_type", [None, "TEMPO", " tempo"])
def test_the_same_kind_of_session_keeps_its_text(workout_type):
    base = _validate(workoutType="tempo", title="SFR", description="SFR 5x6 at 50 rpm")

    merged = schemas.merge_update(
        base, _update(workout_type=workout_type, title="SFR, shorter")
    )

    assert merged.description == "SFR 5x6 at 50 rpm"


def test_a_chat_change_to_rest_drops_the_intervals_it_cancels():
    plan = [
        {
            "date": DATE,
            "workoutType": "intervals",
            "title": "VO2 max 5x4",
            "description": "5x4 min at 120 % FTP",
            "durationMinutes": 75,
            "intervals": [{"duration": 240, "power": 300, "rest": 240}],
        }
    ]

    [day] = plan_pipeline.apply_plan_updates(
        plan,
        [{"date": DATE, "workoutType": "rest", "title": "Rest", "durationMinutes": 0}],
    )

    assert day["description"] == "Rest"
    assert "intervals" not in day
