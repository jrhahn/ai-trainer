"""Texts written before the athlete writes follow the app's language (ai-trainer-ops#181)."""

import pytest
from sqlalchemy import select

import crud
import models
from services import ai_service
from services.prompts import opening_message_system, week_goals_user
from tests.conftest import TestSessionLocal

WEEKS = [{"weekStart": "2026-10-19", "summary": "base week"}]


@pytest.mark.parametrize(
    ("language", "opener", "other"),
    [
        ("English", "For now I'm assuming that", "Ich gehe erst mal davon aus"),
        ("German", "Ich gehe erst mal davon aus, dass", "For now I'm assuming"),
    ],
)
def test_the_opening_names_the_assumption_phrase_in_its_own_language(
    language, opener, other
):
    prompt = opening_message_system(language, "cycling")
    assert opener in prompt
    assert other not in prompt


def test_a_language_without_a_phrase_is_asked_for_in_that_language():
    prompt = opening_message_system("French", "cycling")
    assert "phrased naturally in French" in prompt
    assert "Ich gehe" not in prompt


def test_the_goals_follow_the_app_language_until_the_athlete_writes():
    english = week_goals_user(WEEKS, {}, language="English")
    assert "Write every goal in English, the language of their app." in english

    german = week_goals_user(WEEKS, {}, language="German")
    assert "Write every goal in German" in german

    # Their own words win: the samples are the anchor then.
    written = week_goals_user(
        WEEKS, {}, language="English", language_samples=["Wie lange soll ich fahren?"]
    )
    assert "Write every goal in" not in written
    assert "Wie lange soll ich fahren?" in written

    assert "Write every goal in" not in week_goals_user(WEEKS, {})


@pytest.fixture
def goal_languages(monkeypatch):
    seen: list[str | None] = []

    async def write_week_goals(weeks, profile, **kwargs):
        seen.append(kwargs.get("language"))
        return {w["weekStart"]: "A goal" for w in weeks}

    monkeypatch.setattr(ai_service, "write_week_goals", write_week_goals)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "language"), [("en", "English"), ("de", "German"), (None, None)]
)
async def test_the_dashboard_hands_the_goal_writer_the_app_language(
    client, auth_headers, goal_languages, header, language
):
    headers = {**auth_headers, **({"X-App-Language": header} if header else {})}

    assert (
        await client.post("/api/v1/ai/season-plan/goals", headers=headers)
    ).status_code == 200

    assert goal_languages == [language]


@pytest.mark.asyncio
async def test_the_app_language_reaches_the_prompt_of_a_new_account(
    client, auth_headers, monkeypatch
):
    prompts: list[str] = []

    async def chat(provider, system_prompt, user_msg, **kwargs):
        prompts.append(user_msg)
        return '{"goals": []}'

    monkeypatch.setattr(ai_service, "_chat", chat)
    # The conftest stub replaced the writer; this test needs the real one.
    monkeypatch.setattr(ai_service, "write_week_goals", WRITE_WEEK_GOALS)
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        assert await crud.get_recent_athlete_messages(db, user.id) == []

    await client.post(
        "/api/v1/ai/season-plan/goals", headers={**auth_headers, "X-App-Language": "en"}
    )

    assert prompts and "Write every goal in English" in prompts[0]


WRITE_WEEK_GOALS = ai_service.write_week_goals
