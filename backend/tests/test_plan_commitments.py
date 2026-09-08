"""Arrangements outlive the days they were written on (#667).

2026-09-08, production. The coach set Wednesday to strength and wrote the reason
into the day itself — "keeping lower body load light ahead of Thursday's
interval session" — and hours later an automated write turned Thursday into a
second strength day. Nothing was violated: Wednesday was pinned, Thursday had
never been claimed by anyone. A pin protects the day the coach wrote; it does
not protect the day that day was written *for*.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
import models
from auth import hash_password
from services import plan_commitments, plan_pipeline
from services.dates import app_today
from services.prompts import plan_commitments_section
from tests.conftest import TestSessionLocal


def _day(date: str, workout_type: str = "endurance", title: str | None = None) -> dict:
    return {
        "date": date,
        "workoutType": workout_type,
        "title": title or f"Workout {date}",
        "description": "Session",
        "durationMinutes": 60,
    }


def _dates(count: int = 4, offset: int = 1) -> list[str]:
    return [(app_today() + timedelta(days=offset + i)).isoformat() for i in range(count)]


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


def _by_date(plan: list[dict]) -> dict[str, dict]:
    return {d["date"]: d for d in plan}


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def test_a_reversed_window_is_swapped_rather_than_rejected():
    """The intent is obvious; throwing it away would lose a real arrangement."""
    assert plan_commitments.normalize_window("2026-09-11", "2026-09-09") == (
        "2026-09-09",
        "2026-09-11",
    )


def test_an_unparseable_window_is_dropped():
    assert plan_commitments.normalize_window("next week", "2026-09-11") is None
    assert plan_commitments.normalize_window(None, None) is None


def test_an_over_long_window_is_clamped():
    """One bad response must not freeze the plan for a season."""
    start, end = plan_commitments.normalize_window("2026-09-01", "2027-01-01")
    assert start == "2026-09-01"
    assert end == "2026-09-22"  # start + MAX_COMMITMENT_DAYS


def test_committed_dates_covers_the_window_inclusively():
    dates = plan_commitments.committed_dates(
        [{"startDate": "2026-09-09", "endDate": "2026-09-11"}]
    )
    assert dates == {"2026-09-09", "2026-09-10", "2026-09-11"}


def test_a_single_day_commitment_covers_that_day():
    assert plan_commitments.committed_dates(
        [{"startDate": "2026-09-09", "endDate": "2026-09-09"}]
    ) == {"2026-09-09"}


# ---------------------------------------------------------------------------
# The prompt section
# ---------------------------------------------------------------------------


def test_the_section_is_empty_without_commitments():
    assert plan_commitments_section([], app_today()) == ""
    assert plan_commitments_section(None, app_today()) == ""


def test_the_section_anchors_the_window_to_weekdays():
    """Every prompt that shows dates has to name the weekday (#462/#464/#625)."""
    section = plan_commitments_section(
        [
            {
                "startDate": "2026-09-09",
                "endDate": "2026-09-11",
                "text": "Strength Wednesday so Thursday's intervals land well.",
            }
        ],
        app_today(),
    )
    assert "2026-09-09 (Wednesday)" in section
    assert "2026-09-11 (Friday)" in section
    assert "Strength Wednesday" in section


def test_a_commitment_without_text_contributes_nothing():
    assert (
        plan_commitments_section(
            [{"startDate": "2026-09-09", "endDate": "2026-09-11", "text": "  "}],
            app_today(),
        )
        == ""
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_commitment_supersedes_an_overlapping_one():
    """Agreeing something new for the same window is changing your mind."""
    user_id = await _create_user("commit-supersede@example.com", [])
    async with TestSessionLocal() as db:
        await crud.create_plan_commitment(
            db, user_id, start_date="2026-09-09", end_date="2026-09-11", text="First"
        )
        await crud.create_plan_commitment(
            db, user_id, start_date="2026-09-10", end_date="2026-09-12", text="Second"
        )
        await db.commit()
        active = await crud.list_active_plan_commitments(
            db, user_id, today="2026-09-09"
        )
    assert [c.text for c in active] == ["Second"]


@pytest.mark.asyncio
async def test_a_non_overlapping_commitment_is_kept():
    user_id = await _create_user("commit-parallel@example.com", [])
    async with TestSessionLocal() as db:
        await crud.create_plan_commitment(
            db, user_id, start_date="2026-09-09", end_date="2026-09-10", text="First"
        )
        await crud.create_plan_commitment(
            db, user_id, start_date="2026-09-20", end_date="2026-09-21", text="Second"
        )
        await db.commit()
        active = await crud.list_active_plan_commitments(
            db, user_id, today="2026-09-09"
        )
    assert sorted(c.text for c in active) == ["First", "Second"]


@pytest.mark.asyncio
async def test_a_passed_window_stops_binding_before_anything_sweeps():
    user_id = await _create_user("commit-expiry@example.com", [])
    async with TestSessionLocal() as db:
        await crud.create_plan_commitment(
            db, user_id, start_date="2026-09-09", end_date="2026-09-11", text="Old"
        )
        await db.commit()
        assert await crud.list_active_plan_commitments(
            db, user_id, today="2026-09-12"
        ) == []
        # …and the sweep then retires the row for good.
        assert (
            await crud.deactivate_expired_plan_commitments(
                db, user_id, today="2026-09-12"
            )
            == 1
        )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_automated_write_may_not_change_a_committed_day():
    """The 2026-09-08 regression, expressed as the arrangement it broke."""
    wed, thu, fri = _dates(3)
    plan = [
        _day(wed, "strength", "Core and Upper Body Strength"),
        _day(thu, "intervals", "Sub-Threshold Climbing Intervals"),
        _day(fri, "rest", "Complete Rest Day"),
    ]
    user_id = await _create_user("gate-commit@example.com", plan)
    async with TestSessionLocal() as db:
        await crud.create_plan_commitment(
            db,
            user_id,
            start_date=wed,
            end_date=fri,
            text="Strength Wednesday, intervals Thursday, rest Friday.",
        )
        await db.commit()

    result = await _commit(
        user_id,
        [
            _day(wed, "strength", "Core and Upper Body Strength"),
            _day(thu, "strength", "Core and Upper Body Mobility"),
            _day(fri, "endurance", "Steady Ride"),
        ],
        "generate",
    )

    days = _by_date(result.plan)
    assert days[thu]["workoutType"] == "intervals"
    assert days[fri]["workoutType"] == "rest"


@pytest.mark.asyncio
async def test_the_coach_may_change_a_committed_day():
    """The athlete asking for something new is exactly how a commitment ends."""
    wed, thu = _dates(2)
    plan = [_day(wed, "strength"), _day(thu, "intervals")]
    user_id = await _create_user("gate-commit-coach@example.com", plan)
    async with TestSessionLocal() as db:
        await crud.create_plan_commitment(
            db, user_id, start_date=wed, end_date=thu, text="Keep Thursday's intervals."
        )
        await db.commit()

    result = await _commit(
        user_id, [_day(wed, "strength"), _day(thu, "recovery", "Easy Spin")], "coach_chat"
    )

    assert _by_date(result.plan)[thu]["workoutType"] == "recovery"


@pytest.mark.asyncio
async def test_a_day_outside_the_window_is_still_writable():
    wed, thu, fri = _dates(3)
    plan = [_day(wed, "strength"), _day(thu, "intervals"), _day(fri, "endurance")]
    user_id = await _create_user("gate-commit-outside@example.com", plan)
    async with TestSessionLocal() as db:
        await crud.create_plan_commitment(
            db, user_id, start_date=wed, end_date=thu, text="Wednesday and Thursday."
        )
        await db.commit()

    result = await _commit(
        user_id,
        [_day(wed, "strength"), _day(thu, "intervals"), _day(fri, "recovery", "Easy")],
        "generate",
    )

    assert _by_date(result.plan)[fri]["workoutType"] == "recovery"


@pytest.mark.asyncio
async def test_an_expired_commitment_protects_nothing():
    wed, thu = _dates(2)
    plan = [_day(wed, "strength"), _day(thu, "intervals")]
    user_id = await _create_user("gate-commit-expired@example.com", plan)
    async with TestSessionLocal() as db:
        past = (app_today() - timedelta(days=5)).isoformat()
        await crud.create_plan_commitment(
            db,
            user_id,
            start_date=past,
            end_date=(app_today() - timedelta(days=3)).isoformat(),
            text="Last week's arrangement.",
        )
        await db.commit()

    result = await _commit(
        user_id, [_day(wed, "strength"), _day(thu, "recovery", "Easy")], "generate"
    )

    assert _by_date(result.plan)[thu]["workoutType"] == "recovery"


@pytest.mark.asyncio
async def test_a_hard_constraint_still_wins_over_a_commitment():
    """The athlete saying they are unavailable outranks what they agreed before."""
    wed, thu = _dates(2)
    plan = [_day(wed, "strength"), _day(thu, "intervals")]
    user_id = await _create_user("gate-commit-constraint@example.com", plan)
    async with TestSessionLocal() as db:
        await crud.create_plan_commitment(
            db, user_id, start_date=wed, end_date=thu, text="Intervals on Thursday."
        )
        await crud.upsert_availability_constraint(
            db,
            user_id,
            constraint_type="no_training",
            constraint_date=thu,
            expires_on=thu,
        )
        await db.commit()

    result = await _commit(
        user_id, [_day(wed, "strength"), _day(thu, "intervals")], "generate"
    )

    assert _by_date(result.plan)[thu]["workoutType"] == "rest"


# ---------------------------------------------------------------------------
# The coach's side of the contract
# ---------------------------------------------------------------------------


def test_the_reply_schema_carries_plan_commitment():
    """A field the coach may write that the schema omits never reaches the DB."""
    from services.coach_schema import COACH_REPLY_SCHEMA

    commitment = COACH_REPLY_SCHEMA["properties"]["planCommitment"]
    assert set(commitment["required"]) == {"startDate", "endDate", "text"}
    assert "planCommitment" in COACH_REPLY_SCHEMA["propertyOrdering"]


def test_the_output_contract_asks_for_a_commitment():
    from services.prompts import ask_trainer_plan_updates_rule

    # Both branches — with a session in context and without — or one path
    # silently loses the field.
    for context_workout in (None, {"date": "2026-09-09", "title": "Gym"}):
        rule = ask_trainer_plan_updates_rule(context_workout)
        assert '"planCommitment"' in rule
        # It must also say when *not* to send one, or every change becomes a freeze.
        assert "Omit it" in rule


@pytest.mark.asyncio
async def test_the_coach_reply_records_the_arrangement(
    client, auth_headers, mock_ai_service
):
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Alles klar.",
        "plan_updates": [],
        "planCommitment": {
            "startDate": "2026-04-10",
            "endDate": "2026-04-12",
            "text": "Krafttraining am Freitag, damit Samstag die Intervalle stehen.",
        },
        "sources": [],
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Passt das so?"},
    )

    assert response.status_code == 200
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        active = await crud.list_active_plan_commitments(
            db, user.id, today="2026-04-10"
        )
    assert [c.text for c in active] == [
        "Krafttraining am Freitag, damit Samstag die Intervalle stehen."
    ]


@pytest.mark.asyncio
async def test_an_unusable_commitment_never_costs_the_plan_change(
    client, auth_headers, mock_ai_service
):
    """Dropping a malformed arrangement must not take the edit down with it."""
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Rest tomorrow.",
        "plan_updates": [
            {
                "date": "2026-04-10",
                "workoutType": "rest",
                "title": "Rest Day",
                "description": "Full rest",
                "durationMinutes": 0,
            }
        ],
        "planCommitment": {"startDate": "sometime", "endDate": "", "text": "..."},
        "sources": [],
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Can I rest tomorrow?"},
    )

    assert response.status_code == 200
    assert response.json()["planUpdates"][0]["workoutType"] == "rest"
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        assert (
            await crud.list_active_plan_commitments(db, user.id, today="2026-04-10")
            == []
        )


@pytest.mark.asyncio
async def test_a_plan_write_survives_a_commitment_read_failure(monkeypatch):
    """Commitments make writes safer; failing to read one must not block one."""
    wed, thu = _dates(2)
    plan = [_day(wed, "strength"), _day(thu, "intervals")]
    user_id = await _create_user("gate-commit-broken@example.com", plan)

    async def boom(*args, **kwargs):
        raise RuntimeError("table is missing")

    monkeypatch.setattr(crud, "list_active_plan_commitments", boom)

    result = await _commit(
        user_id, [_day(wed, "strength"), _day(thu, "recovery", "Easy")], "generate"
    )

    assert _by_date(result.plan)[thu]["workoutType"] == "recovery"
