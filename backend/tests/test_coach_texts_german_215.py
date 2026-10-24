"""The coach's own texts in the athlete's interface language (ai-trainer-ops#215).

Hypotheses, open questions, experiments and learned traits are stored in
English, because the engine reads them, and a German tester read them so: on
the coach timeline right after onboarding, and in Settings. They now go
through the plan's translations (#121), and the fixed starting hypotheses
have a German text of their own.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
from services import ai_service, starting_hypotheses
from tests.conftest import TestSessionLocal

GERMAN = {"X-App-Language": "de"}
NOW = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
OLD_RATIONALE = (
    "Starting assumption from the onboarding answers (ai-trainer-ops#154); the "
    "athlete's answer and the first rides test it."
)


@pytest.fixture
def fake_translator(monkeypatch):
    calls: list[list[str]] = []

    async def translate(texts, language, provider="openai"):
        calls.append(list(texts))
        return {text: f"DE {text}" for text in texts}

    monkeypatch.setattr(ai_service, "translate_plan_texts", translate)
    return calls


async def _rider_id(db) -> str:
    return (await crud.get_user_by_email(db, "rider@example.com")).id


@pytest.mark.asyncio
async def test_the_coachs_texts_are_translated_with_the_plan(client, auth_headers, fake_translator):
    async with TestSessionLocal() as db:
        user_id = await _rider_id(db)
        await crud.propose_athlete_hypothesis(
            db, user_id, statement="Recovers quickly between hard climbing intervals",
            rationale="Seen in two rides.", observed_at=NOW,
        )
        await crud.record_athlete_open_question(
            db, user_id, question="Does sleep change your power?", evidence="Two short nights, two flat rides.",
            needs="A week of sleep notes.", observed_at=NOW,
        )
        await crud.suggest_athlete_experiment(
            db, user_id, question="Does a later dinner help?", protocol="Eat at 8 pm for a week.",
            rationale="Your evening rides fade.", observed_at=NOW,
        )
        fact = await crud.observe_athlete_memory_fact(db, user_id, fact="Rides best in the morning", observed_at=NOW)
        await crud.record_athlete_memory_fact_contradiction(db, fact, reason="Your last three PRs were evening rides.", now=NOW)
        await db.commit()

    before = (await client.get("/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN})).json()
    written = (await client.post("/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN})).json()

    assert before == {"language": "de", "translations": {}, "missing": True}
    texts = [
        "Recovers quickly between hard climbing intervals",
        "Seen in two rides.",
        "Does sleep change your power?",
        "Two short nights, two flat rides.",
        "A week of sleep notes.",
        "Does a later dinner help?",
        "Eat at 8 pm for a week.",
        "Your evening rides fade.",
        "Rides best in the morning",
        "Your last three PRs were evening rides.",
    ]
    assert fake_translator == [texts]
    assert written == {"language": "de", "translations": {t: f"DE {t}" for t in texts}, "missing": False}


@pytest.mark.asyncio
async def test_the_fixed_starting_hypotheses_read_german_without_a_call(client, auth_headers, fake_translator):
    fixed = starting_hypotheses.fallback({"level": "beginner"}, "German")
    async with TestSessionLocal() as db:
        await starting_hypotheses.store(db, await _rider_id(db), fixed, language="German", now=NOW)
        await db.commit()

    got = (await client.get("/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN})).json()

    assert got == {
        "language": "de",
        "translations": {
            "Has ridden under 3 hours a week over the last two months": (
                "Du bist in den letzten zwei Monaten unter 3 Stunden pro Woche gefahren"
            ),
            "Has not trained to a structured plan before": "Du hast noch nie nach einem strukturierten Plan trainiert",
            starting_hypotheses._RATIONALE: (
                "Eine Annahme aus deinen Antworten beim Start. Deine Antwort und die ersten Fahrten prüfen sie."
            ),
        },
        "missing": False,
    }
    assert fake_translator == []


@pytest.mark.parametrize("level", ["beginner", "intermediate", "advanced"])
def test_every_fixed_text_has_its_german_text(level):
    """Keyed by the exact English text, so a reworded one would lose its German."""
    for hypothesis in starting_hypotheses.fallback({"level": level}, "German"):
        assert hypothesis.statement in starting_hypotheses.GERMAN
    assert starting_hypotheses._RATIONALE in starting_hypotheses.GERMAN


@pytest.mark.asyncio
async def test_no_athlete_reads_an_issue_number(client, auth_headers, fake_translator):
    """A starting hypothesis stored before the fix still carries "(ai-trainer-ops#154)"."""
    async with TestSessionLocal() as db:
        user_id = await _rider_id(db)
        await starting_hypotheses.store(
            db, user_id, starting_hypotheses.fallback({}, "German"), language="German", now=NOW
        )
        await crud.propose_athlete_hypothesis(
            db, user_id, statement="Can hold threshold for 20 minutes", rationale=OLD_RATIONALE, observed_at=NOW
        )
        await db.commit()

    for path in (
        "/api/v1/users/me/athlete-hypotheses",
        "/api/v1/users/me/athlete-memory-facts",
        "/api/v1/users/me/open-questions",
        "/api/v1/users/me/inquiries",
        "/api/v1/users/me/validation-experiments",
        "/api/v1/users/me/chat",
        "/api/v1/ai/plan-translations",
    ):
        for headers in (auth_headers, {**auth_headers, **GERMAN}):
            resp = await client.get(path, headers=headers)
            assert resp.status_code == 200, path
            assert "ai-trainer-ops#" not in resp.text, path

    hypotheses = (await client.get("/api/v1/users/me/athlete-hypotheses", headers=auth_headers)).json()["hypotheses"]
    assert {h["rationale"] for h in hypotheses} == {starting_hypotheses._RATIONALE}
    # So the old rows read German too: the stripped rationale is the fixed one.
    translations = (await client.get("/api/v1/ai/plan-translations", headers={**auth_headers, **GERMAN})).json()
    assert translations["translations"][starting_hypotheses._RATIONALE].startswith("Eine Annahme")
