"""The nightly run commits its plan writes before each model call (review of
2026-11-01, C2, ai-trainer-ops#338).

The run marked the done sessions, asked the model to adapt, wrote the plan,
asked the model for the narration and then for the translation, all in one
transaction: the plan row stayed locked (#282) through three model calls, and
a chat reply that night waited for them.
"""

from __future__ import annotations

import pytest

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
