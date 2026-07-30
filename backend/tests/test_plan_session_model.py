"""Pure-function tests for the two-a-day session model (#496).

Kept apart from ``test_plan_sessions.py``, which covers the same feature through
the pipeline and the database. These are pure functions — the session identity
itself, its serialization contract, and the prompt annotations — so they stay
fast and readable without any fixture setup.
"""

from __future__ import annotations


import schemas
from services.dates import annotate_plan_days


def _session(
    date: str,
    workout_type: str = "endurance",
    *,
    slot: int | None = None,
    duration: int = 60,
    time_of_day: str | None = None,
) -> dict:
    day: dict = {
        "date": date,
        "workoutType": workout_type,
        "title": f"{workout_type} {date}",
        "description": "Session",
        "durationMinutes": duration,
    }
    if slot is not None:
        day["slot"] = slot
    if time_of_day is not None:
        day["timeOfDay"] = time_of_day
    return day


# ---------------------------------------------------------------------------
# Session identity
# ---------------------------------------------------------------------------


def test_legacy_day_reads_as_slot_zero():
    """A stored day predating two-a-days is the day's first session."""
    assert schemas.day_slot({"date": "2026-08-04"}) == 0
    assert schemas.session_key({"date": "2026-08-04"}) == ("2026-08-04", 0)


def test_slot_zero_is_not_serialized():
    """Slot 0 stays absent on the wire, so stored single-session days don't churn.

    Emitting ``slot: 0`` would rewrite every existing plan on the first commit
    after #496 and make an unchanged plan compare unequal to the database —
    turning every no-op write into a real write that cascades downstream.
    """
    dumped = schemas.PlanDay.model_validate(_session("2026-08-04")).model_dump(
        by_alias=True, exclude_none=True
    )
    assert "slot" not in dumped

    with_slot = schemas.PlanDay.model_validate(
        _session("2026-08-04", slot=1)
    ).model_dump(by_alias=True, exclude_none=True)
    assert with_slot["slot"] == 1


def test_normalize_slot_is_total():
    """A slot is a storage key, so no input may raise or invent a session.

    ``object()`` stands in for anything defining ``__int__`` — notably a test
    double — which a bare ``int(value)`` would happily turn into a real-looking
    slot 1 and silently mis-key a session.
    """
    assert schemas.normalize_slot(None) == 0
    assert schemas.normalize_slot("") == 0
    assert schemas.normalize_slot("2") == 2
    assert schemas.normalize_slot(-3) == 0
    assert schemas.normalize_slot(True) == 0
    assert schemas.normalize_slot(object()) == 0
    assert schemas.normalize_slot(1.9) == 1


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def test_annotate_plan_days_labels_sessions_within_a_day():
    """The coach sees AM/PM ordering instead of two indistinguishable entries."""
    date = "2026-08-11"
    annotated = annotate_plan_days(
        [
            _session(date, "endurance", slot=1, time_of_day="pm"),
            _session(date, "recovery", slot=0, time_of_day="am"),
        ]
    )
    # Emitted in slot order regardless of the caller's ordering.
    assert [d["workoutType"] for d in annotated] == ["recovery", "endurance"]
    assert annotated[0]["sessionOrder"] == 1
    assert annotated[0]["sessionCount"] == 2
    assert "am" in annotated[0]["sessionLabel"]
    assert annotated[1]["sessionOrder"] == 2


def test_annotate_plan_days_adds_no_session_labels_for_one_session():
    """A single-session day's prompt payload is unchanged — no phantom ordering."""
    annotated = annotate_plan_days([_session("2026-08-12", "endurance")])
    assert "sessionOrder" not in annotated[0]
    assert "sessionLabel" not in annotated[0]
