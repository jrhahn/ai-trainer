"""The offers under a coach reply survive a reload (review of 2026-11-01,
ai-trainer-ops#338 T1).

A reply's buttons (a race to add or move, a race to remove, a profile change)
lived only in the browser. After a reload the coach's text still pointed at
them and nothing was there. They are stored with the reply now, returned with
the history, and the athlete's answer is kept so an answered offer is not
offered again.
"""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

import crud
import models
from services import coach_chat
from services.dates import app_today
from tests.conftest import TestSessionLocal

ASK = "/api/v1/ai/ask-trainer"
CHAT = "/api/v1/users/me/chat"


def _resolve(message_id: str, kind: str) -> str:
    return f"{CHAT}/{message_id}/offers/{kind}/resolve"


async def _user() -> models.User:
    async with TestSessionLocal() as db:
        return await db.scalar(select(models.User))


async def _with_a_race(days_ahead: int = 70) -> str:
    race_day = (app_today() + timedelta(days=days_ahead)).isoformat()
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_goal, user.race_date = "race", race_day
        await db.commit()
    return race_day


async def _ask_to_move_the_race(client, auth_headers) -> dict:
    reply = await client.post(
        ASK,
        headers=auth_headers,
        json={"question": "Das Rennen wird verschoben, es ist jetzt in 3 Wochen."},
    )
    assert reply.status_code == 200
    return reply.json()


@pytest.mark.asyncio
async def test_the_offer_is_stored_with_the_reply_and_comes_back_with_the_history(
    client, auth_headers, mock_ai_service
):
    race_day = await _with_a_race()
    body = await _ask_to_move_the_race(client, auth_headers)
    moved_to = (app_today() + timedelta(days=21)).isoformat()

    async with TestSessionLocal() as db:
        row = await db.get(models.ChatMessage, body["messageId"])
    assert row.role == "assistant"
    assert row.offers_json == {
        "raceOffer": {
            "date": moved_to,
            "replaces": {"date": race_day, "kind": "profile", "eventId": None},
        },
        "resolved": {},
    }
    assert body["raceOffer"] == row.offers_json["raceOffer"]

    history = (await client.get(CHAT, headers=auth_headers)).json()["messages"]
    user_msg, reply = history
    assert user_msg["offers"] is None and user_msg["id"]
    assert reply["id"] == body["messageId"]
    assert reply["offers"] == {
        "raceOffer": row.offers_json["raceOffer"],
        "raceProposal": None,
        "profileProposal": None,
        "resolved": {},
    }


@pytest.mark.asyncio
async def test_a_reply_without_an_offer_stores_none(
    client, auth_headers, mock_ai_service
):
    reply = await client.post(
        ASK, headers=auth_headers, json={"question": "Wie war meine Woche?"}
    )

    async with TestSessionLocal() as db:
        row = await db.get(models.ChatMessage, reply.json()["messageId"])
    assert row.offers_json is None
    history = (await client.get(CHAT, headers=auth_headers)).json()["messages"]
    assert [m["offers"] for m in history] == [None, None]


@pytest.mark.asyncio
async def test_a_race_proposal_is_stored_in_the_response_shape(
    client, auth_headers, mock_ai_service
):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_goal = "race"
        user.race_date = "2027-05-13"
        user.race_description = "80km 4000hm"
        await db.commit()

    body = (
        await client.post(
            ASK,
            headers=auth_headers,
            json={"question": "Ich habe kein Rennen mehr geplant."},
        )
    ).json()

    history = (await client.get(CHAT, headers=auth_headers)).json()["messages"]
    assert (
        history[-1]["offers"]["raceProposal"]
        == body["raceProposal"]
        == {
            "raceDate": "2027-05-13",
            "raceDescription": "80km 4000hm",
            "calendarRaces": 0,
        }
    )


def test_every_kind_of_offer_is_stored_camel_case():
    captured = coach_chat.chat_capture.Captured(
        plan=[],
        profile={},
        availability_constraints=[],
        lifted_constraints=[],
        captured_constraints=[],
        new_location=None,
        captured_goals=[],
        captured_states=[],
        illness_restored={},
        illness_easy_until=None,
        heart_rate_stripped=False,
        red_flag=None,
        heart_rate_newly_off=False,
        race_proposal={"race_date": "2027-05-13", "calendar_races": 2},
        race_offer={"date": "2027-01-17", "replaces": None},
        profile_proposal={"weekly_hours": 6, "ftp": 250},
        captured_day_preferences=[],
    )

    stored = coach_chat.offers_for_storage(captured)

    assert stored["raceOffer"] == {"date": "2027-01-17", "replaces": None}
    assert stored["raceProposal"] == {
        "raceDate": "2027-05-13",
        "raceDescription": None,
        "calendarRaces": 2,
    }
    assert stored["profileProposal"]["weeklyHours"] == 6
    assert stored["profileProposal"]["ftp"] == 250
    assert stored["resolved"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["accepted", "dismissed"])
async def test_an_answered_offer_is_kept_as_answered(
    client, auth_headers, mock_ai_service, outcome
):
    await _with_a_race()
    body = await _ask_to_move_the_race(client, auth_headers)

    resolved = await client.post(
        _resolve(body["messageId"], "raceOffer"),
        headers=auth_headers,
        json={"outcome": outcome},
    )

    assert resolved.status_code == 200
    assert resolved.json()["resolved"] == {"raceOffer": outcome}
    history = (await client.get(CHAT, headers=auth_headers)).json()["messages"]
    assert history[-1]["offers"]["resolved"] == {"raceOffer": outcome}
    assert history[-1]["offers"]["raceOffer"] == body["raceOffer"]


@pytest.mark.asyncio
async def test_the_first_answer_stands(client, auth_headers, mock_ai_service):
    await _with_a_race()
    body = await _ask_to_move_the_race(client, auth_headers)
    url = _resolve(body["messageId"], "raceOffer")

    first = await client.post(url, headers=auth_headers, json={"outcome": "accepted"})
    again = await client.post(url, headers=auth_headers, json={"outcome": "accepted"})
    other = await client.post(url, headers=auth_headers, json={"outcome": "dismissed"})

    assert [r.status_code for r in (first, again, other)] == [200, 200, 200]
    assert other.json()["resolved"] == {"raceOffer": "accepted"}
    async with TestSessionLocal() as db:
        row = await db.get(models.ChatMessage, body["messageId"])
    assert row.offers_json["resolved"] == {"raceOffer": "accepted"}


@pytest.mark.asyncio
async def test_another_athletes_reply_is_not_found(
    client, auth_headers, mock_ai_service
):
    await _with_a_race()
    body = await _ask_to_move_the_race(client, auth_headers)
    other = await client.post(
        "/api/v1/auth/register",
        json={"name": "Other", "email": "other@example.com", "password": "Str0ng!Pass"},
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

    response = await client.post(
        _resolve(body["messageId"], "raceOffer"),
        headers=other_headers,
        json={"outcome": "dismissed"},
    )

    assert response.status_code == 404
    async with TestSessionLocal() as db:
        row = await db.get(models.ChatMessage, body["messageId"])
    assert row.offers_json["resolved"] == {}


@pytest.mark.asyncio
async def test_an_offer_the_reply_does_not_carry_is_not_found(
    client, auth_headers, mock_ai_service
):
    await _with_a_race()
    body = await _ask_to_move_the_race(client, auth_headers)
    plain = (
        await client.post(
            ASK, headers=auth_headers, json={"question": "Wie war meine Woche?"}
        )
    ).json()

    no_such_kind = await client.post(
        _resolve(body["messageId"], "profileProposal"),
        headers=auth_headers,
        json={"outcome": "accepted"},
    )
    no_offers = await client.post(
        _resolve(plain["messageId"], "raceOffer"),
        headers=auth_headers,
        json={"outcome": "accepted"},
    )
    no_message = await client.post(
        _resolve("not-a-message", "raceOffer"),
        headers=auth_headers,
        json={"outcome": "accepted"},
    )

    assert [r.status_code for r in (no_such_kind, no_offers, no_message)] == [
        404,
        404,
        404,
    ]


@pytest.mark.asyncio
async def test_a_bad_kind_or_outcome_is_refused(client, auth_headers, mock_ai_service):
    await _with_a_race()
    body = await _ask_to_move_the_race(client, auth_headers)

    bad_kind = await client.post(
        _resolve(body["messageId"], "capturedGoals"),
        headers=auth_headers,
        json={"outcome": "accepted"},
    )
    bad_outcome = await client.post(
        _resolve(body["messageId"], "raceOffer"),
        headers=auth_headers,
        json={"outcome": "maybe"},
    )
    long_id = await client.post(
        _resolve("x" * 37, "raceOffer"),
        headers=auth_headers,
        json={"outcome": "accepted"},
    )

    assert [r.status_code for r in (bad_kind, bad_outcome, long_id)] == [
        422,
        422,
        422,
    ]


@pytest.mark.asyncio
async def test_resolving_needs_a_login(client):
    response = await client.post(
        _resolve("any", "raceOffer"), json={"outcome": "accepted"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_crud_keeps_the_rest_of_the_offers(db):
    user = await crud.create_user(
        db, email="crud-offers@example.com", name="Rider", hashed_password="x"
    )
    message = await crud.create_chat_message(
        db, user.id, role="assistant", content="Hi", timestamp="2026-11-01T10:00:00"
    )
    message.offers_json = {
        "raceOffer": {"date": "2027-01-17", "replaces": None},
        "profileProposal": {"weeklyHours": 6},
        "resolved": {"raceOffer": "dismissed"},
    }
    await db.flush()

    offers = await crud.resolve_chat_offer(
        db, user.id, message.id, "profileProposal", "accepted"
    )

    assert offers == {
        "raceOffer": {"date": "2027-01-17", "replaces": None},
        "profileProposal": {"weeklyHours": 6},
        "resolved": {"raceOffer": "dismissed", "profileProposal": "accepted"},
    }


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "20261101_000002_chat_offers.py"
)


def _run(engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_chat_offers", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(migration, direction)()


def _columns(engine) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns("chat_messages")}


def test_the_column_is_added_once_and_dropped_on_the_way_down(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE chat_messages (id VARCHAR(36) PRIMARY KEY, "
                "content TEXT NOT NULL)"
            )
        )
        conn.execute(sa.text("INSERT INTO chat_messages VALUES ('a', 'kept')"))

    _run(engine, "upgrade")
    _run(engine, "upgrade")  # a second run finds it there and does nothing
    assert _columns(engine) == {"id", "content", "offers_json"}

    _run(engine, "downgrade")
    assert _columns(engine) == {"id", "content"}
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT content FROM chat_messages")).scalar() == (
            "kept"
        )
    _run(engine, "downgrade")  # nothing left to drop
    assert _columns(engine) == {"id", "content"}


def test_the_migration_follows_the_last_head():
    spec = importlib.util.spec_from_file_location("_chat_offers", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == (
        "20261101_000002",
        "20261101_000001",
    )


def test_the_model_declares_the_column():
    """The test database is built from the models, not the migrations."""
    column = models.ChatMessage.__table__.columns["offers_json"]
    assert column.nullable
