"""The nightly run commits its plan writes before each model call (review of
2026-11-01, C2, ai-trainer-ops#338).

The run marked the done sessions, asked the model to adapt, wrote the plan,
asked the model for the narration and then for the translation, all in one
transaction: the plan row stayed locked (#282) through three model calls, and
a chat reply that night waited for them.
"""

from __future__ import annotations

import pytest

import crud
from auth import decode_token
from services.dates import app_today
from tests.test_clearance_hold_338 import _day as _hold_day
from tests.test_red_flags_338 import ASK

from services import coach_summary, plan_maintenance, plan_pipeline, plan_translations
from tests.conftest import TestSessionLocal
from tests.test_nightly_plan_translations_271 import NOW, _reads, night  # noqa: F401
from tests.test_plan_maintenance import _create_user_with_plan, _day


@pytest.mark.asyncio
async def test_each_model_call_of_the_night_follows_a_release(night, monkeypatch):  # noqa: F811
    steps: list[str] = []

    def spy(name, real):
        async def record(*args, **kwargs):
            steps.append(name)
            return await real(*args, **kwargs)

        return record

    adapt = plan_maintenance.ai_service.adapt_training_plan
    monkeypatch.setattr(
        plan_maintenance.ai_service, "adapt_training_plan", spy("adapt", adapt)
    )
    monkeypatch.setattr(
        plan_pipeline,
        "release_plan_lock",
        spy("release", plan_pipeline.release_plan_lock),
    )
    monkeypatch.setattr(
        plan_pipeline, "commit_plan", spy("write", plan_pipeline.commit_plan)
    )
    monkeypatch.setattr(
        coach_summary,
        "narrate_plan_changes",
        spy("narrate", coach_summary.narrate_plan_changes),
    )
    monkeypatch.setattr(
        plan_translations,
        "translate_new_plan",
        spy("translate", plan_translations.translate_new_plan),
    )
    user_id = await _create_user_with_plan(
        email="night-c2@example.com", plan=[_day("2026-06-08"), _day("2026-06-10")]
    )
    await _reads(user_id, "German")

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert result.updated == 1
    assert steps == [
        "release",
        "adapt",
        "write",
        "release",
        "narrate",
        "release",
        "translate",
    ]


async def _ask_with_a_plan(client, auth_headers, mock_ai_service, monkeypatch, text):
    steps: list[str] = []
    release = plan_pipeline.release_plan_lock

    async def spy_release(db):
        steps.append("release")
        await release(db)

    def answer(*args, **kwargs):
        steps.append("model")
        return mock_ai_service["ask_trainer"].return_value

    monkeypatch.setattr(plan_pipeline, "release_plan_lock", spy_release)
    mock_ai_service["ask_trainer"].side_effect = answer
    today = app_today()
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [_hold_day(today, 1)])
        await db.commit()

    response = await client.post(ASK, headers=auth_headers, json={"question": text})

    assert response.status_code == 200
    return steps


@pytest.mark.asyncio
async def test_a_chat_that_holds_the_plan_commits_before_the_model_answers(
    client, auth_headers, mock_ai_service, monkeypatch
):
    steps = await _ask_with_a_plan(
        client,
        auth_headers,
        mock_ai_service,
        monkeypatch,
        "Ich hatte beim Bergauffahren Brustschmerzen.",
    )

    assert steps == ["release", "model"]


@pytest.mark.asyncio
async def test_a_chat_that_writes_no_plan_commits_nothing_early(
    client, auth_headers, mock_ai_service, monkeypatch
):
    steps = await _ask_with_a_plan(
        client, auth_headers, mock_ai_service, monkeypatch, "Schöne Runde heute."
    )

    assert steps == ["model"]
