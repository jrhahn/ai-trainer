"""The coach speaks first once onboarding is done (ai-trainer-ops#153)."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

import models
from services import ai_service, opening_message, prompts
from services.dates import app_today
from tests.conftest import TestSessionLocal

TODAY = date(2026, 10, 19)  # a Monday


class _NoonOnTheMonday(datetime):
    """``services.dates``'s clock, held at noon of the Monday above."""

    @classmethod
    def now(cls, tz=None):
        moment = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def one_date_for_the_test_and_the_server(monkeypatch):
    """The test writes the plan around ``app_today()``, and the server reads the
    date again when it builds the opening facts. A run across midnight in Berlin
    saw two dates: the facts' first day was no longer the plan's rest day (CI,
    2026-10-25 23:00Z, ``test_a_failing_model_still_leaves_the_fixed_message``).
    """
    import services.dates

    monkeypatch.setattr(services.dates, "datetime", _NoonOnTheMonday)


def _plan(first_type: str = "rest", today: date = TODAY) -> list[dict]:
    def on(offset: int) -> str:
        return (today + timedelta(days=offset)).isoformat()

    return [
        {
            "date": on(-1),
            "workoutType": "endurance",
            "title": "Yesterday",
            "durationMinutes": 60,
        },
        {
            "date": on(0),
            "workoutType": first_type,
            "title": "Rest" if first_type == "rest" else "Spin",
            "durationMinutes": 0 if first_type == "rest" else 45,
        },
        {
            "date": on(2),
            "workoutType": "endurance",
            "title": "Easy ride",
            "durationMinutes": 60,
        },
        {
            "date": on(7),
            "workoutType": "endurance",
            "title": "Next week",
            "durationMinutes": 90,
        },
    ]


async def _onboard(client, headers, *, language: str | None = "de"):
    extra = {"X-App-Language": language} if language else {}
    await client.put(
        "/api/v1/users/me",
        json={
            "trainingGoal": "general_fitness",
            "trainingDays": ["wednesday", "saturday"],
            "sports": ["cycling"],
        },
        headers=headers,
    )
    plan = {"plan": _plan(today=app_today())}
    assert (
        await client.put("/api/v1/users/me/plan", json=plan, headers=headers)
    ).status_code == 200
    return await client.put(
        "/api/v1/users/me", json={"isOnboarded": True}, headers={**headers, **extra}
    )


async def _coach_messages() -> list[models.ChatMessage]:
    async with TestSessionLocal() as db:
        return list(
            (
                await db.scalars(
                    select(models.ChatMessage).where(
                        models.ChatMessage.role == "assistant"
                    )
                )
            ).all()
        )


# Two usable starting assumptions, as the model returns them (ai-trainer-ops#154).
MODEL_HYPOTHESES = [
    {
        "category": "weekly_volume",
        "statement": "Has ridden 3 to 6 hours a week over the last two months",
        "question": "Wie viele Stunden pro Woche bist du zuletzt gefahren?",
        "options": [
            {"label": "unter 3 h", "stance": "refutes"},
            {"label": "3–6 h", "stance": "confirms"},
            {"label": "mehr als 6 h", "stance": "refutes"},
        ],
    },
    {
        "category": "recovery",
        "statement": "Recovers within a day from a hard session",
        "question": "Wie fühlst du dich am Tag nach einer harten Einheit?",
        "options": [
            {"label": "Frisch", "stance": "confirms"},
            {"label": "Noch müde", "stance": "refutes"},
            {"label": "Weiß nicht", "stance": "neutral"},
        ],
    },
]


# The plan-text translation's German for the assumption the fixed texts do not
# hold (ai-trainer-ops#230).
GERMAN_STATEMENTS = {
    "Recovers within a day from a hard session": (
        "Erholt sich innerhalb eines Tages von einer harten Einheit"
    ),
}


async def fake_translation(texts, language, provider="openai"):
    return {
        text: GERMAN_STATEMENTS[text] for text in texts if text in GERMAN_STATEMENTS
    }


@pytest.fixture
def model_reply(monkeypatch):
    calls: list[dict] = []
    reply = {"text": "Hallo, ich bin dein Coach."}

    async def fake(facts, language, sport, provider="openai"):
        calls.append({"facts": facts, "language": language, "sport": sport})
        if isinstance(reply["text"], Exception):
            raise reply["text"]
        return reply["text"], reply.get("hypotheses", MODEL_HYPOTHESES)

    monkeypatch.setattr(ai_service, "write_opening_message", fake)
    monkeypatch.setattr(ai_service, "translate_plan_texts", fake_translation)
    return calls, reply


@pytest.mark.asyncio
async def test_finishing_onboarding_posts_one_coach_message_in_the_interface_language(
    client, auth_headers, model_reply
):
    calls, _ = model_reply

    assert (await _onboard(client, auth_headers)).status_code == 200

    [message] = await _coach_messages()
    # The model's three parts; the app lists the assumptions under them (#230).
    assert message.content.split("\n\n")[0] == "Hallo, ich bin dein Coach."
    recovery = MODEL_HYPOTHESES[1]["statement"]
    assert f"- {GERMAN_STATEMENTS[recovery]}" in message.content
    assert [c["language"] for c in calls] == ["German"]
    assert calls[0]["sport"] == "cycling"
    # The interface's words, not the stored values (ai-trainer-ops#190).
    assert calls[0]["facts"]["answers"]["trainingDays"] == ["Mittwoch", "Samstag"]
    assert calls[0]["facts"]["first_day_is_rest"] is True
    history = (await client.get("/api/v1/users/me/chat", headers=auth_headers)).json()[
        "messages"
    ]
    assert [m["role"] for m in history] == ["assistant"]


@pytest.mark.asyncio
async def test_a_second_onboarded_write_posts_nothing_more(
    client, auth_headers, model_reply
):
    calls, _ = model_reply
    await _onboard(client, auth_headers)

    await client.put(
        "/api/v1/users/me", json={"isOnboarded": True}, headers=auth_headers
    )
    await client.put("/api/v1/users/me", json={"weeklyHours": 6}, headers=auth_headers)

    assert len(await _coach_messages()) == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_profile_write_before_onboarding_posts_nothing(
    client, auth_headers, model_reply
):
    calls, _ = model_reply

    await client.put("/api/v1/users/me", json={"weeklyHours": 6}, headers=auth_headers)
    await client.put(
        "/api/v1/users/me", json={"isOnboarded": False}, headers=auth_headers
    )

    assert await _coach_messages() == []
    assert calls == []


@pytest.mark.asyncio
async def test_an_account_already_marked_gets_none(client, auth_headers, model_reply):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.opening_message_at = datetime.now(timezone.utc)
        await db.commit()

    await _onboard(client, auth_headers)

    assert await _coach_messages() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("provider down"), asyncio.TimeoutError()]
)
async def test_a_failing_model_still_leaves_the_fixed_message(
    client, auth_headers, model_reply, failure
):
    calls, reply = model_reply
    reply["text"] = failure

    assert (await _onboard(client, auth_headers)).status_code == 200

    [message] = await _coach_messages()
    assert message.content.startswith("Hallo! Dein erster Plan steht.")
    # The reason the facts give, which depends on today's weekday (#230).
    reason = calls[0]["facts"]["first_day_reason"]
    assert {
        "not_a_training_day": "Heute ist keiner deiner Trainingstage, also ein Ruhetag.",
        "eases_in": "Heute ist deshalb ein Ruhetag.",
    }[reason] in message.content


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "x" * (opening_message.MAX_MESSAGE_CHARS + 1)])
async def test_an_empty_or_overlong_reply_is_replaced(
    client, auth_headers, model_reply, text
):
    _, reply = model_reply
    reply["text"] = text

    await _onboard(client, auth_headers, language=None)

    [message] = await _coach_messages()
    assert message.content.startswith("Hi! Your first plan is ready.")


@pytest.mark.asyncio
async def test_a_spent_budget_falls_back_without_asking_the_model(
    client, auth_headers, model_reply, monkeypatch
):
    calls, _ = model_reply
    from services import token_accounting

    async def over_budget(db, user, *, source):
        raise token_accounting.TokenBudgetExceededError("spent")

    monkeypatch.setattr(token_accounting, "enforce_token_budget", over_budget)

    assert (await _onboard(client, auth_headers)).status_code == 200

    [message] = await _coach_messages()
    assert message.content.startswith("Hallo!")
    assert calls == []


@pytest.mark.asyncio
async def test_the_model_is_asked_before_the_request_writes_anything(
    client, auth_headers, monkeypatch
):
    """No lock is held while the model runs (review of app PR #969)."""
    seen: list[bool | None] = []

    async def fake(facts, language, sport, provider="openai"):
        async with TestSessionLocal() as other:
            user = await other.scalar(select(models.User))
            seen.append(user.is_onboarded)
            # Another request can still write the account meanwhile.
            user.weekly_hours = 7
            await other.commit()
        return "Hallo.", MODEL_HYPOTHESES

    monkeypatch.setattr(ai_service, "write_opening_message", fake)

    assert (await _onboard(client, auth_headers)).status_code == 200

    assert seen == [False]
    [message] = await _coach_messages()
    assert message.content.startswith("Hallo.\n\n")


@pytest.mark.asyncio
async def test_a_failing_write_still_finishes_onboarding(
    client, auth_headers, model_reply, monkeypatch
):
    import crud

    async def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(crud, "create_chat_message", broken)

    response = await _onboard(client, auth_headers)

    assert response.status_code == 200
    assert response.json()["isOnboarded"] is True
    assert await _coach_messages() == []
    async with TestSessionLocal() as db:
        # The claim went with the failed write, not on its own.
        assert (await db.scalar(select(models.User))).opening_message_at is None


@pytest.mark.asyncio
async def test_two_writes_racing_post_one_message(client, auth_headers):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        first = await opening_message.post_opening_message(
            db, user, opening_message.OpeningMessage("Eins", [], "German")
        )
        second = await opening_message.post_opening_message(
            db, user, opening_message.OpeningMessage("Zwei", [], "German")
        )
        await db.commit()

    assert first is not None and second is None
    assert [m.content for m in await _coach_messages()] == ["Eins"]


@pytest.mark.asyncio
async def test_a_plan_that_cannot_be_read_still_gets_a_message(
    client, auth_headers, model_reply, monkeypatch
):
    import crud

    calls, _ = model_reply
    real = crud.get_training_plan
    broken = {"on": False}

    async def maybe_broken(db, user_id):
        if broken["on"]:
            raise RuntimeError("plan table gone")
        return await real(db, user_id)

    monkeypatch.setattr(crud, "get_training_plan", maybe_broken)
    plan = {"plan": _plan(today=app_today())}
    assert (
        await client.put("/api/v1/users/me/plan", json=plan, headers=auth_headers)
    ).status_code == 200
    broken["on"] = True

    response = await client.put(
        "/api/v1/users/me",
        json={"isOnboarded": True},
        headers={**auth_headers, "X-App-Language": "de"},
    )

    assert response.status_code == 200

    [message] = await _coach_messages()
    assert message.content.split("\n\n")[0] == "Hallo, ich bin dein Coach."
    assert calls[0]["facts"]["firstWeek"] == []


@pytest.mark.asyncio
async def test_a_real_sql_error_on_the_plan_read_still_finishes_onboarding(
    client, auth_headers, model_reply, monkeypatch
):
    """A database error, not a Python one: the savepoint has to take it."""
    import crud

    calls, _ = model_reply
    real = crud.get_training_plan
    broken = {"on": False}

    async def maybe_broken(db, user_id):
        if broken["on"]:
            await db.execute(sa.text("SELECT * FROM no_such_table"))
        return await real(db, user_id)

    monkeypatch.setattr(crud, "get_training_plan", maybe_broken)
    plan = {"plan": _plan(today=app_today())}
    assert (
        await client.put("/api/v1/users/me/plan", json=plan, headers=auth_headers)
    ).status_code == 200
    broken["on"] = True

    response = await client.put(
        "/api/v1/users/me",
        json={"isOnboarded": True},
        headers={**auth_headers, "X-App-Language": "de"},
    )

    assert response.status_code == 200
    assert response.json()["isOnboarded"] is True
    assert len(await _coach_messages()) == 1
    assert calls[0]["facts"]["firstWeek"] == []


def test_the_facts_hold_the_answers_and_only_the_first_week():
    user = models.User(
        sports=["cycling"],
        training_goal="race",
        race_date="2027-05-12",
        race_description="Gran Fondo",
        training_days=["Wed", "Sat"],
        fitness_level="intermediate",
        current_ftp=250,
    )

    facts = opening_message.build_facts(user, _plan(), TODAY)

    assert facts["answers"] == {
        "sports": ["cycling"],
        "goal": "race",
        "raceDate": "2027-05-12",
        "race": "Gran Fondo",
        "trainingDays": ["Wed", "Sat"],
        "level": "intermediate",
        "ftpWatts": 250,
    }
    assert [d["date"] for d in facts["firstWeek"]] == ["2026-10-19", "2026-10-21"]
    assert facts["firstWeek"][0]["weekday"] == "Mon"
    assert facts["first_day_is_rest"] is True
    assert facts["examples"] == list(opening_message.EXAMPLES)


def test_a_session_today_is_not_a_rest_day_and_no_plan_is_none():
    user = models.User(sports=["running"], training_goal="general_fitness")

    assert (
        opening_message.build_facts(user, _plan("endurance"), TODAY)[
            "first_day_is_rest"
        ]
        is False
    )
    empty = opening_message.build_facts(user, None, TODAY)
    assert empty["firstWeek"] == [] and empty["first_day_is_rest"] is False
    assert empty["answers"] == {"sports": ["running"], "goal": "general fitness"}


def test_first_days_skips_what_is_not_a_dated_day():
    plan = [
        None,
        {"date": "soon"},
        {"workoutType": "rest"},
        {"date": "2026-10-20", "workout_type": "intervals"},
    ]

    assert opening_message.first_days(plan, TODAY) == [
        {
            "date": "2026-10-20",
            "weekday": "Tue",
            "workoutType": "intervals",
            "title": "",
            "durationMinutes": 0,
        }
    ]


@pytest.mark.parametrize(
    ("sports", "word"),
    [
        (["running"], "running"),
        (["cycling", "running"], "multisport endurance"),
        ([], "cycling"),
        (None, "cycling"),
    ],
)
def test_the_coach_names_the_sport_the_athlete_chose(sports, word):
    assert opening_message._sport_word(sports) == word


def test_the_fixed_text_names_a_race_and_skips_the_rest_line_on_a_training_day():
    facts = {
        "answers": {"goal": "race", "trainingDays": ["Sat"]},
        "first_day_is_rest": False,
    }

    english = opening_message.fixed_message(facts, None)
    german = opening_message.fixed_message(facts, "German")

    assert "your training days and your race" in english
    assert "rest day" not in english
    assert "deinem Rennen" in german and "Ruhetag" not in german
    assert all(f"„{e}“" in german for e in opening_message._EXAMPLES_DE)


def test_the_prompt_asks_for_the_language_the_three_parts_and_no_invention():
    system = prompts.opening_message_system("German", "cycling")

    assert "in German" in system and "du" in system
    assert "FACTS.first_day_is_rest" in system and "FACTS.examples" in system
    assert "Never invent a number" in system
    assert str(prompts.OPENING_MESSAGE_MAX_WORDS) in system
    assert "FACTS:" in prompts.opening_message_user({"answers": {"goal": "race"}})


@pytest.mark.asyncio
async def test_the_model_call_reads_the_message_and_ignores_a_broken_reply(monkeypatch):
    replies = iter(
        [
            '{"message": "  Hallo!  "}',
            '{"other": 1}',
            "not json",
            '{"message": 3, "hypotheses": "no"}',
        ]
    )

    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, task=None):
        return next(replies)

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    facts = {"answers": {}}

    assert await ai_service.write_opening_message(facts, "German", "cycling") == (
        "Hallo!",
        [],
    )
    assert await ai_service.write_opening_message(facts, "German", "cycling") == (
        "",
        [],
    )
    assert await ai_service.write_opening_message(facts, "German", "cycling") == (
        "",
        [],
    )
    assert await ai_service.write_opening_message(facts, "German", "cycling") == (
        "",
        [],
    )


def test_the_migration_adds_the_column_once_and_marks_onboarded_accounts(tmp_path):
    path = (
        Path(__file__).parent.parent
        / "alembic/versions/20261019_000001_opening_message.py"
    )
    spec = importlib.util.spec_from_file_location("_opening_message_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'opening.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, is_onboarded BOOLEAN)"
            )
        )
        conn.execute(sa.text("INSERT INTO users VALUES ('old', 1), ('new', 0)"))

    def run(direction: str) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()

    run("upgrade")
    run("upgrade")
    with engine.begin() as conn:
        marked = dict(
            conn.execute(
                sa.text("SELECT id, opening_message_at IS NOT NULL FROM users")
            ).all()
        )
    assert marked == {"old": 1, "new": 0}
    run("downgrade")
    run("downgrade")
    assert "opening_message_at" not in {
        c["name"] for c in sa.inspect(engine).get_columns("users")
    }


@pytest.mark.asyncio
async def test_the_model_call_hands_back_the_assumptions_unchecked(monkeypatch):
    async def fake_chat(provider, system_prompt, user_msg, json_mode=False, task=None):
        return '{"message": "Hallo!", "hypotheses": [{"category": "nonsense"}]}'

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    assert await ai_service.write_opening_message({}, "German", "cycling") == (
        "Hallo!",
        [{"category": "nonsense"}],
    )


def test_the_prompt_asks_for_two_testable_assumptions_in_the_language():
    system = prompts.opening_message_system("German", "cycling")

    assert '"hypotheses"' in system and "weekly_volume" in system
    assert "at most 140 characters" in system
    # The app lists them under the message, with the pinned line as it stands
    # (ai-trainer-ops#230): the model is told not to name them itself.
    assert "do not name them in it" in system
    assert prompts.PINNED_QUESTIONS_LINE["German"] not in system
    assert prompts.PINNED_QUESTIONS_LINE["German"] in (
        opening_message.assumptions_block(["Erholt sich schnell"], "German")
    )
