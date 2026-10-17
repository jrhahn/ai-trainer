"""What the chat captured or proposes, for the athlete to confirm (ai-trainer-ops#65, #71)."""

import pytest
from sqlalchemy import select

import crud
import models
from services.race_intent import says_no_race
from tests.conftest import TestSessionLocal


@pytest.mark.parametrize(
    "message",
    [
        # From the owner's chat, paraphrased.
        "also, i dont have any race event coming up, its just general fitness",
        "übrigens ist das 4000hm Ziel erst mal weg",
        "Ich habe kein Rennen mehr geplant.",
        "I have no race planned",
        "Das Rennen ist abgesagt.",
    ],
)
def test_saying_there_is_no_race(message):
    assert says_no_race(message)


@pytest.mark.parametrize(
    "message",
    [
        "Is there no race?",
        "no race this weekend?",
        "the race was hard",
        "i dont have the race data yet",
    ],
)
def test_not_saying_it(message):
    assert not says_no_race(message)


async def _user() -> models.User:
    async with TestSessionLocal() as db:
        return await db.scalar(select(models.User))


@pytest.mark.asyncio
async def test_a_captured_goal_comes_back_and_can_be_undone(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Mein Ziel ist 4000 hm an einem Tag."},
    )

    [captured] = response.json()["capturedGoals"]
    assert captured["label"] == "4,000 m of climbing in one ride"
    assert response.json()["raceProposal"] is None

    undone = await client.delete(
        f"/api/v1/users/me/goals/{captured['id']}", headers=auth_headers
    )
    assert undone.status_code == 200
    async with TestSessionLocal() as db:
        assert await crud.list_active_goals(db, (await _user()).id) == []

    missing = await client.delete(
        "/api/v1/users/me/goals/not-a-goal", headers=auth_headers
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_no_race_is_proposed_not_applied_and_cleared_on_confirm(
    client, auth_headers, mock_ai_service
):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_goal = "race"
        user.race_date = "2027-05-13"
        user.race_description = "80km 4000hm"
        await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ich habe kein Rennen mehr geplant."},
    )

    assert response.json()["raceProposal"] == {
        "raceDate": "2027-05-13",
        "raceDescription": "80km 4000hm",
        "calendarRaces": 0,
    }
    assert (await _user()).race_date == "2027-05-13"  # only offered

    cleared = await client.post("/api/v1/users/me/race/clear", headers=auth_headers)
    assert cleared.status_code == 200
    user = await _user()
    assert (user.race_date, user.race_description, user.training_goal) == (
        None,
        None,
        "general_fitness",
    )


@pytest.mark.asyncio
async def test_no_proposal_without_a_race_on_the_profile(
    client, auth_headers, mock_ai_service
):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_goal = "general_fitness"
        user.race_date = None
        await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I have no race planned"},
    )

    assert response.json()["raceProposal"] is None


@pytest.mark.asyncio
async def test_clearing_also_removes_upcoming_calendar_races_so_no_sync_brings_it_back(
    client, auth_headers, mock_ai_service
):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        for day in ("2020-05-01", "2099-05-01"):
            db.add(
                models.RaceEvent(
                    user_id=user.id, date=day, distance_km=80, elevation_m=4000
                )
            )
        user.training_goal = "race"
        user.race_date = "2099-05-01"
        user.race_description = "Calendar race: 80 km with 4000 m climbing"
        await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I have no race planned"},
    )
    assert response.json()["raceProposal"]["calendarRaces"] == 1

    cleared = await client.post("/api/v1/users/me/race/clear", headers=auth_headers)
    assert cleared.json() == {"status": "cleared", "removedRaceEvents": 1}

    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        dates = [event.date for event in await crud.get_race_events(db, user.id)]
    assert dates == ["2020-05-01"]  # history stays
    assert (user.race_date, user.training_goal) == (None, "general_fitness")


@pytest.mark.asyncio
async def test_clearing_without_a_race_changes_nothing_else(client, auth_headers):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_goal = "general_fitness"
        user.race_date = None
        await db.commit()

    cleared = await client.post("/api/v1/users/me/race/clear", headers=auth_headers)

    assert cleared.json() == {"status": "cleared", "removedRaceEvents": 0}
    assert (await _user()).training_goal == "general_fitness"


@pytest.mark.asyncio
async def test_only_a_goal_in_force_can_be_removed(client, auth_headers):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        goal = await crud.upsert_active_goal(
            db, user.id, metric="distance_km", scope="ride", target=200
        )
        goal.status = "achieved"
        await db.commit()
        goal_id = goal.id

    response = await client.delete(
        f"/api/v1/users/me/goals/{goal_id}", headers=auth_headers
    )

    assert response.status_code == 404


# --- "No race" for the year said (ai-trainer-ops#129) --------------------------------

from datetime import date, timedelta  # noqa: E402

from services.dates import app_today  # noqa: E402
from services.race_intent import NoRaceScope, no_race_scope  # noqa: E402

OCTOBER = date(2026, 10, 17)


@pytest.mark.parametrize(
    ("message", "scope"),
    [
        ("kein Rennen dieses Jahr, aber nächstes", NoRaceScope(None, date(2026, 12, 31))),
        ("Ich habe kein Rennen mehr.", NoRaceScope()),
        ("Nächstes Jahr kein Rennen.", NoRaceScope(date(2027, 1, 1), date(2027, 12, 31))),
        ("no race in 2028", NoRaceScope(date(2028, 1, 1), date(2028, 12, 31))),
        # The year in another clause is not the one without a race.
        ("Ich fahre 2027 wieder, dieses Jahr kein Rennen.", NoRaceScope(None, date(2026, 12, 31))),
        ("Wie war das Rennen?", None),
    ],
)
def test_no_race_is_about_the_year_said(message, scope):
    assert no_race_scope(message, OCTOBER) == scope


async def _race_this_year(next_years_race: bool) -> None:
    today = app_today()
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_goal = "race"
        user.race_date = date(today.year, 12, 31).isoformat()
        user.race_description = "Season finale"
        if next_years_race:
            db.add(
                models.RaceEvent(
                    user_id=user.id,
                    date=date(today.year + 1, 6, 1).isoformat(),
                    distance_km=120,
                    elevation_m=2000,
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_no_race_this_year_does_not_offer_to_delete_next_years(
    client, auth_headers, mock_ai_service
):
    """The button clears every upcoming race, next year's included."""
    await _race_this_year(next_years_race=True)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ich habe kein Rennen dieses Jahr, aber nächstes."},
    )

    assert response.json()["raceProposal"] is None


@pytest.mark.asyncio
async def test_no_race_this_year_offers_to_clear_this_years(
    client, auth_headers, mock_ai_service
):
    await _race_this_year(next_years_race=False)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ich habe kein Rennen dieses Jahr, aber nächstes."},
    )

    assert response.json()["raceProposal"]["raceDescription"] == "Season finale"


@pytest.mark.asyncio
async def test_no_race_next_year_leaves_this_years_race_alone(
    client, auth_headers, mock_ai_service
):
    await _race_this_year(next_years_race=False)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Nächstes Jahr fahre ich kein Rennen."},
    )

    assert response.json()["raceProposal"] is None

