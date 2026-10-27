"""The sessions an illness took come back when the athlete is well again (ai-trainer-ops#232).

Mia's run: ill, three days became "Rest Day - Illness"; well again, only one came
back. The owner's (a): every one comes back from the plan history, easy inside
the easy days, and the reply names them.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

import crud
import models
from services import illness_return, plan_pipeline, plan_translations, rest_days
from domain.athlete_states import KIND_ILL, RETURN_EASY_DAYS, StatedState
from services.dates import app_today
from tests.conftest import TestSessionLocal, notes_text
from tests.test_athlete_states import _user_id

WEEKDAYS_EN = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
ALL_DAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
ILLNESS_REST = {
    "workoutType": "rest",
    "title": "Rest Day - Illness",
    "description": "Rest so your body can fight the infection.",
    "durationMinutes": 0,
    "workoutPurpose": "Recover from the illness.",
    "keyFocusPoints": ["Sleep", "Drink"],
}


def _iso(days: int) -> str:
    return (app_today() + timedelta(days=days)).isoformat()


def _sessions() -> list[dict]:
    return [
        {
            "date": _iso(1),
            "workoutType": "vo2max",
            "title": "VO2max intervals",
            "description": "5 x 3 min hard at 300 W, 3 min easy between.",
            "durationMinutes": 60,
            "intervals": [{"duration": 180, "power": 300, "rest": 180}] * 5,
            "workoutPurpose": "Raise your ceiling.",
            "keyFocusPoints": ["Go hard on every rep"],
        },
        {
            "date": _iso(4),
            "workoutType": "threshold",
            "title": "Threshold 2 x 20",
            "description": "2 x 20 min at threshold.",
            "durationMinutes": 75,
        },
        {
            "date": _iso(5),
            "workoutType": "endurance",
            "title": "Long ride",
            "description": "Steady Z2.",
            "durationMinutes": 90,
        },
    ]


async def _ill_and_rested(rested_days: list[int], source: str = "coach_chat") -> str:
    """Mia's message 3: the illness is stored, then the coach rests days for it."""
    user_id = await _user_id()
    today = app_today()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        user.training_days = ALL_DAYS
        await crud.upsert_training_plan(db, user_id, _sessions())
        await crud.record_state(
            db, user_id, StatedState(KIND_ILL, today - timedelta(days=1), None), today
        )
        await db.commit()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": _iso(offset), **ILLNESS_REST} for offset in rested_days],
            base_plan=_sessions(),
            source=source,
        )
        await db.commit()
    return user_id


async def _stored(user_id: str) -> dict[str, dict]:
    async with TestSessionLocal() as db:
        plan = (await crud.get_training_plan(db, user_id)).plan
    return {day["date"]: day for day in plan}


@pytest.mark.asyncio
async def test_well_again_gives_back_every_day_the_illness_took(
    client, auth_headers, mock_ai_service
):
    user_id = await _ill_and_rested([1, 4])
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Good to hear.",
        "plan_updates": None,
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Feeling better, no fever for two days. Can I train again?"},
    )

    assert response.status_code == 200
    stored = await _stored(user_id)
    # Inside the easy days (until yesterday + RETURN_EASY_DAYS): easy, no longer.
    easy_until = app_today() - timedelta(days=1) + timedelta(days=RETURN_EASY_DAYS)
    assert _iso(1) <= easy_until.isoformat() < _iso(4)
    easy = stored[_iso(1)]
    assert easy["workoutType"] == "recovery"
    assert easy["title"] == "Easy ride"
    assert easy["description"] == illness_return.EASY_DESCRIPTION
    assert easy["durationMinutes"] == 60
    assert not easy.get("intervals") and not easy.get("targetPower")
    assert not easy.get("workoutPurpose") and not easy.get("keyFocusPoints")
    # After them: the session as it was.
    back = stored[_iso(4)]
    assert (back["workoutType"], back["title"], back["durationMinutes"]) == (
        "threshold",
        "Threshold 2 x 20",
        75,
    )
    assert stored[_iso(5)]["title"] == "Long ride"
    body = response.json()
    assert notes_text(body) == (
        f"These sessions are back in your plan: "
        f"{WEEKDAYS_EN[(app_today() + timedelta(days=1)).weekday()]} {_iso(1)} (easy), "
        f"{WEEKDAYS_EN[(app_today() + timedelta(days=4)).weekday()]} {_iso(4)}."
    )
    assert {day["date"] for day in body["updatedPlan"]} >= {_iso(1), _iso(4)}
    # The history names the restore as its own source.
    async with TestSessionLocal() as db:
        rows = (
            await db.scalars(
                select(models.PlanDayHistory).where(
                    models.PlanDayHistory.source == "illness_return"
                )
            )
        ).all()
    assert sorted(row.date for row in rows) == [_iso(1), _iso(4)]


@pytest.mark.asyncio
async def test_the_note_is_german_for_a_german_athlete(
    client, auth_headers, mock_ai_service
):
    await _ill_and_rested([4])
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Schön.",
        "plan_updates": None,
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers={**auth_headers, "X-App-Language": "de"},
        json={"question": "Bin wieder gesund."},
    )

    weekday = [
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ][(app_today() + timedelta(days=4)).weekday()]
    assert notes_text(response.json()) == (
        f"Diese Einheiten sind wieder in deinem Plan: {weekday} {_iso(4)}."
    )


@pytest.mark.asyncio
async def test_only_the_illness_rest_days_come_back(
    client, auth_headers, mock_ai_service
):
    """Not a day changed since, not a rest day the guard wrote for its own reason,
    and nothing without an ended illness."""
    user_id = await _ill_and_rested([1, 4])
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        # The coach already brought day 1 back as a short spin.
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [
                {
                    "date": _iso(1),
                    "workoutType": "recovery",
                    "title": "Easy Return Spin",
                    "durationMinutes": 30,
                }
            ],
            base_plan=current,
            source="coach_chat",
        )
        # And a rest day with the guard's own reason, written during the illness.
        guarded = rest_days._as_rest(_sessions()[2], rest_days.OFF_DAY_REASON)
        plan = [
            guarded if day["date"] == _iso(5) else day
            for day in (await crud.get_training_plan(db, user_id)).plan
        ]
        await crud.upsert_training_plan(db, user_id, plan)
        db.add(
            models.PlanDayHistory(
                user_id=user_id,
                date=_iso(5),
                slot=0,
                old_day=_sessions()[2],
                new_day=guarded,
                source="generate",
                applied=True,
            )
        )
        await db.commit()
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Good.",
        "plan_updates": None,
    }

    # A message that ends no illness changes nothing.
    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "How was my week?"},
    )
    assert (await _stored(user_id))[_iso(4)]["title"] == "Rest Day - Illness"

    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I'm feeling better."},
    )

    stored = await _stored(user_id)
    assert stored[_iso(1)]["title"] == "Easy Return Spin"
    assert stored[_iso(4)]["title"] == "Threshold 2 x 20"
    assert stored[_iso(5)]["description"] == rest_days.OFF_DAY_REASON


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["history_rebuild", "history_review"])
async def test_the_illness_rest_days_of_a_connect_come_back(
    client, auth_headers, mock_ai_service, source
):
    """A connect rebuilds the plan, and an athlete ill at the time gets rest days
    from it as from any generation (ai-trainer-ops#306, review of #1188)."""
    user_id = await _ill_and_rested([4], source=source)
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Good.",
        "plan_updates": None,
    }

    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I'm feeling better."},
    )

    assert (await _stored(user_id))[_iso(4)]["title"] == "Threshold 2 x 20"


@pytest.mark.asyncio
async def test_a_rest_day_the_athlete_chose_while_ill_stays(
    client, auth_headers, mock_ai_service
):
    """Review of #1057: a trip asked for in the chat, or a day set in the editor,
    is the athlete's own rest day, even inside the illness."""
    user_id = await _ill_and_rested([1])
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [
                {
                    "date": _iso(4),
                    **ILLNESS_REST,
                    "title": "Rest Day - Travel",
                    "description": "You are away.",
                    "workoutPurpose": None,
                }
            ],
            base_plan=current,
            source="coach_chat",
        )
        current = (await crud.get_training_plan(db, user_id)).plan
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": _iso(5), **ILLNESS_REST}],
            base_plan=current,
            source="user_edit",
        )
        await db.commit()
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Good.",
        "plan_updates": None,
    }

    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I'm feeling better."},
    )

    stored = await _stored(user_id)
    assert stored[_iso(1)]["title"] == "Easy ride"
    assert stored[_iso(4)]["title"] == "Rest Day - Travel"
    assert rest_days.is_rest(stored[_iso(5)])


@pytest.mark.asyncio
async def test_a_day_that_is_no_training_day_stays_rest(
    client, auth_headers, mock_ai_service
):
    user_id = await _ill_and_rested([4])
    weekday = ALL_DAYS[(app_today() + timedelta(days=4)).weekday()]
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        user.training_days = [d for d in ALL_DAYS if d != weekday]
        await db.commit()
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Good.",
        "plan_updates": None,
    }

    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I'm feeling better."},
    )

    assert rest_days.is_rest((await _stored(user_id))[_iso(4)])


def test_every_easy_text_has_its_german_text():
    german = plan_translations.FIXED["de"]
    for text in [
        *illness_return.EASY_TITLES.values(),
        illness_return.EASY_TITLE,
        illness_return.EASY_DESCRIPTION,
    ]:
        assert german[text] == illness_return.GERMAN[text]
    assert german["Easy ride"] == "Lockere Ausfahrt"
