"""A new athlete's starting assumptions and the questions that test them
(ai-trainer-ops#154, #155)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

import models
from services import ai_service, starting_hypotheses
from services.dates import app_today
from tests.conftest import TestSessionLocal
from tests.test_opening_message_153 import MODEL_HYPOTHESES, _plan

INQUIRIES = "/api/v1/users/me/inquiries"


def _hyp(**overrides) -> dict:
    return {**MODEL_HYPOTHESES[0], **overrides}


# --- parsing --------------------------------------------------------------


def test_two_usable_assumptions_are_kept_as_they_are():
    parsed = starting_hypotheses.parse(MODEL_HYPOTHESES)

    assert [h.category for h in parsed] == ["weekly_volume", "recovery"]
    assert parsed[0].options[1] == {"label": "3–6 h", "stance": "confirms"}


@pytest.mark.parametrize(
    "broken",
    [
        _hyp(category="favourite_colour"),
        _hyp(statement="x" * 141),
        _hyp(statement=""),
        _hyp(question="Warum? " * 40),
        _hyp(options=[{"label": "Ja", "stance": "confirms"}]),
        _hyp(options=[{"label": f"{i} h", "stance": "refutes"} for i in range(5)]),
        _hyp(
            options=[
                {"label": "Ja", "stance": "neutral"},
                {"label": "Nein", "stance": "neutral"},
            ]
        ),
        _hyp(
            options=[
                {"label": "Ja", "stance": "confirms"},
                {"label": "ja", "stance": "refutes"},
            ]
        ),
        _hyp(
            options=[
                {"label": "Ja", "stance": "maybe"},
                {"label": "Nein", "stance": "refutes"},
            ]
        ),
        _hyp(
            options=[
                {"label": "x" * 41, "stance": "confirms"},
                {"label": "Nein", "stance": "refutes"},
            ]
        ),
        "not a dict",
    ],
)
def test_an_assumption_that_does_not_fit_is_dropped_whole(broken):
    assert starting_hypotheses.parse([broken]) == []


def test_one_per_category_and_two_at_most():
    third = _hyp(category="experience")
    parsed = starting_hypotheses.parse(
        [MODEL_HYPOTHESES[0], _hyp(), MODEL_HYPOTHESES[1], third]
    )

    assert [h.category for h in parsed] == ["weekly_volume", "recovery"]
    assert starting_hypotheses.parse("no list") == []


def test_the_fixed_assumptions_follow_the_level_and_the_language():
    beginner = starting_hypotheses.fallback({"level": "beginner"}, "German")
    middle = starting_hypotheses.fallback({}, None)

    assert (
        beginner[0].statement
        == "Has ridden under 3 hours a week over the last two months"
    )
    assert {o["label"]: o["stance"] for o in beginner[0].options}[
        "unter 3 h"
    ] == "confirms"
    assert beginner[0].question.startswith("Wie viele Stunden")
    # A beginner is assumed new to structured training; "never" confirms that.
    assert {o["label"]: o["stance"] for o in beginner[1].options}[
        "Nein, noch nie"
    ] == "confirms"
    assert {o["label"]: o["stance"] for o in middle[0].options} == {
        "under 3 h": "refutes",
        "3–6 h": "confirms",
        "over 6 h": "refutes",
    }
    assert {o["label"]: o["stance"] for o in middle[1].options}[
        "Yes, regularly"
    ] == "confirms"
    assert (
        starting_hypotheses.parse(
            [
                {
                    "category": h.category,
                    "statement": h.statement,
                    "question": h.question,
                    "options": h.options,
                }
                for h in beginner
            ]
        )
        == beginner
    )


# --- onboarding stores them -------------------------------------------------


async def _onboard(client, headers, reply):
    """Finish onboarding with the model answering ``reply``; the pinned questions."""

    async def fake(facts, language, sport, provider="openai"):
        return reply

    await client.put(
        "/api/v1/users/me", json={"fitnessLevel": "beginner"}, headers=headers
    )
    await client.put(
        "/api/v1/users/me/plan",
        json={"plan": _plan(today=app_today())},
        headers=headers,
    )
    from tests.test_opening_message_153 import fake_translation

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ai_service, "write_opening_message", fake)
        patch.setattr(ai_service, "translate_plan_texts", fake_translation)
        response = await client.put(
            "/api/v1/users/me",
            json={"isOnboarded": True},
            headers={**headers, "X-App-Language": "de"},
        )
    assert response.status_code == 200
    return (await client.get(INQUIRIES, headers=headers)).json()["inquiries"]


async def _hypotheses() -> dict[str, models.AthleteHypothesis]:
    async with TestSessionLocal() as db:
        return {
            h.id: h for h in (await db.scalars(select(models.AthleteHypothesis))).all()
        }


@pytest.mark.asyncio
async def test_onboarding_pins_one_question_per_assumption(client, auth_headers):
    inquiries = await _onboard(
        client, auth_headers, ("Hallo, ich gehe erst mal davon aus …", MODEL_HYPOTHESES)
    )

    assert [i["question"] for i in inquiries] == [
        h["question"] for h in MODEL_HYPOTHESES
    ]
    # Labels only: what an answer means for the assumption stays on the server.
    assert inquiries[0]["options"] == ["unter 3 h", "3–6 h", "mehr als 6 h"]
    assert inquiries[0]["whyAsking"] == "Damit dein Plan von Anfang an zu dir passt."
    hypotheses = await _hypotheses()
    assert sorted(h.category for h in hypotheses.values()) == [
        "starting_recovery",
        "starting_weekly_volume",
    ]
    assert all(
        h.status == "proposed" and h.confidence == pytest.approx(0.3)
        for h in hypotheses.values()
    )
    async with TestSessionLocal() as db:
        rows = (await db.scalars(select(models.AthleteInquiry))).all()
    assert {r.hypothesis_id for r in rows} == set(hypotheses)


@pytest.mark.asyncio
async def test_without_usable_assumptions_the_fixed_ones_go_out_under_the_message(
    client, auth_headers
):
    """The message names no assumption since the app lists them under it, so
    it stays the model's (ai-trainer-ops#230)."""
    inquiries = await _onboard(
        client, auth_headers, ("Eine schöne Nachricht.", [{"category": "nonsense"}])
    )

    assert [i["question"][:16] for i in inquiries] == [
        "Wie viele Stunde",
        "Hast du schon ei",
    ]
    async with TestSessionLocal() as db:
        [message] = (await db.scalars(select(models.ChatMessage))).all()
    assert message.content.startswith("Eine schöne Nachricht.\n\n")
    assert "oben im Chat angeheftet" in message.content


@pytest.mark.asyncio
async def test_a_single_usable_assumption_is_not_enough(client, auth_headers):
    """Two are listed under every message (review of #977): with one usable,
    the fixed two go out, under the model's message (ai-trainer-ops#230)."""
    inquiries = await _onboard(
        client, auth_headers, ("Hallo, ich bin dein Coach.", MODEL_HYPOTHESES[:1])
    )

    assert [i["question"][:16] for i in inquiries] == [
        "Wie viele Stunde",
        "Hast du schon ei",
    ]
    async with TestSessionLocal() as db:
        [message] = (await db.scalars(select(models.ChatMessage))).all()
    assert message.content.startswith("Hallo, ich bin dein Coach.\n\n")


@pytest.mark.asyncio
async def test_assumptions_that_cannot_be_stored_leave_the_message(
    client, auth_headers, monkeypatch
):
    async def broken(*args, **kwargs):
        raise RuntimeError("table gone")

    monkeypatch.setattr(starting_hypotheses.crud, "propose_athlete_hypothesis", broken)

    inquiries = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))

    assert inquiries == []
    async with TestSessionLocal() as db:
        assert len((await db.scalars(select(models.ChatMessage))).all()) == 1


@pytest.mark.asyncio
async def test_a_full_channel_gets_no_question_about_an_unrecorded_assumption(
    client, auth_headers, monkeypatch
):
    async def full(*args, **kwargs):
        return None

    monkeypatch.setattr(starting_hypotheses.crud, "propose_athlete_hypothesis", full)

    assert await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES)) == []


# --- answering with a tap (#155) ---------------------------------------------


@pytest.fixture
def no_judge(monkeypatch):
    calls = []

    async def judge(*args, **kwargs):
        calls.append(args)
        return {
            "answered": True,
            "fact": "Rides mornings.",
            "reply": "Danke!",
            "question": "",
            "asks_coach": False,
        }

    monkeypatch.setattr(ai_service, "evaluate_inquiry_answer", judge)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(("option", "status"), [(1, "confirmed"), (0, "refuted")])
async def test_a_tapped_answer_settles_the_assumption_without_a_model(
    client, auth_headers, no_judge, option, status
):
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))

    response = await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": "whatever the button said", "option": option},
        headers=auth_headers,
    )

    body = response.json()
    assert response.status_code == 200
    assert body["accepted"] is True and body["coachReply"] == ""
    assert body["inquiry"]["status"] == "answered"
    assert no_judge == []
    [hypothesis] = [
        h
        for h in (await _hypotheses()).values()
        if h.category == "starting_weekly_volume"
    ]
    assert hypothesis.status == status
    async with TestSessionLocal() as db:
        chat = [
            (m.role, m.content)
            for m in (
                await db.scalars(
                    select(models.ChatMessage).order_by(models.ChatMessage.created_at)
                )
            ).all()
        ]
        facts = [
            f.fact for f in (await db.scalars(select(models.AthleteMemoryFact))).all()
        ]
    # The stored label, not whatever the client sent along with the index.
    assert chat[-2:] == [
        ("assistant", volume["question"]),
        ("user", volume["options"][option]),
    ]
    assert any(volume["options"][option] in f for f in facts)


@pytest.mark.asyncio
async def test_a_tap_still_answers_when_the_assumption_was_deleted(
    client, auth_headers, no_judge
):
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))
    async with TestSessionLocal() as db:
        for hypothesis in (await db.scalars(select(models.AthleteHypothesis))).all():
            await db.delete(hypothesis)
        await db.commit()

    response = await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": "3–6 h", "option": 1},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["inquiry"]["status"] == "answered"


@pytest.mark.asyncio
async def test_a_neutral_tap_leaves_the_assumption_to_the_rides(
    client, auth_headers, no_judge
):
    [_, recovery] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))

    await client.post(
        f"{INQUIRIES}/{recovery['id']}/answer",
        json={"answer": "Weiß nicht", "option": 2},
        headers=auth_headers,
    )

    [hypothesis] = [
        h for h in (await _hypotheses()).values() if h.category == "starting_recovery"
    ]
    assert hypothesis.status == "proposed"


@pytest.mark.asyncio
@pytest.mark.parametrize("option", [3, 99])
async def test_a_tap_that_is_not_an_answer_is_refused(
    client, auth_headers, no_judge, option
):
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))

    response = await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": "x", "option": option},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert (await client.get(INQUIRIES, headers=auth_headers)).json()["inquiries"][0][
        "status"
    ] == "pending"


@pytest.mark.asyncio
async def test_a_negative_option_is_refused_by_the_schema(
    client, auth_headers, no_judge
):
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))

    response = await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": "x", "option": -1},
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_typing_an_answer_still_goes_to_the_judge(client, auth_headers, no_judge):
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))

    response = await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": "Etwa fünf Stunden"},
        headers=auth_headers,
    )

    assert response.json()["coachReply"] == "Danke!"
    assert len(no_judge) == 1


def test_the_migration_adds_both_columns_once(tmp_path):
    path = (
        Path(__file__).parent.parent
        / "alembic/versions/20261019_000002_inquiry_options.py"
    )
    spec = importlib.util.spec_from_file_location("_inquiry_options_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'inquiries.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text("CREATE TABLE athlete_inquiries (id VARCHAR(36) PRIMARY KEY)")
        )

    def run(direction: str) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()

    def columns() -> set[str]:
        return {c["name"] for c in sa.inspect(engine).get_columns("athlete_inquiries")}

    run("upgrade")
    run("upgrade")
    assert {"options", "hypothesis_id"} <= columns()
    assert migration._INDEX in {
        i["name"] for i in sa.inspect(engine).get_indexes("athlete_inquiries")
    }
    run("downgrade")
    run("downgrade")
    assert not {"options", "hypothesis_id"} & columns()
