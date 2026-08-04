from __future__ import annotations

from datetime import datetime, timezone

import pytest

import models
from auth import hash_password
from services import (
    contradiction_detection,
    hypothesis_generation,
    insight_generation,
    learning_pipeline,
    open_question_generation,
)
from tests.conftest import TestSessionLocal

_NOW = datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc)


async def _create_user(*, email: str, onboarded: bool = True) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Learn Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=onboarded,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
        )
        db.add(user)
        await db.commit()
        return user.id


def _patch_steps(monkeypatch, *, insights=0, contradictions=0, hypotheses=0, questions=0):
    """Replace each per-athlete learning pass with a counting stub."""
    calls: dict[str, dict] = {}

    def make(name: str, value):
        async def fake(db, user, *, now, timezone_name):
            calls[name] = {"now": now, "timezone_name": timezone_name}
            if isinstance(value, Exception):
                raise value
            return value

        return fake

    monkeypatch.setattr(
        insight_generation, "generate_user_insights", make("insights", insights)
    )
    monkeypatch.setattr(
        contradiction_detection,
        "detect_user_contradictions",
        make("contradictions", contradictions),
    )
    monkeypatch.setattr(
        hypothesis_generation,
        "generate_user_hypotheses",
        make("hypotheses", hypotheses),
    )
    monkeypatch.setattr(
        open_question_generation,
        "generate_user_open_questions",
        make("questions", questions),
    )
    return calls


@pytest.mark.asyncio
async def test_run_learning_step_aggregates_every_pass(monkeypatch):
    user_id = await _create_user(email="agg@example.com")
    calls = _patch_steps(
        monkeypatch, insights=1, contradictions=2, hypotheses=3, questions=4
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        result = await learning_pipeline.run_learning_step(
            db, user, now=_NOW, timezone_name="UTC"
        )

    assert (result.observations, result.contradictions) == (1, 2)
    assert (result.hypotheses, result.open_questions) == (3, 4)
    assert result.failed_steps == []
    assert result.changed is True
    # Every pass ran and received the shared now/timezone context.
    assert set(calls) == {"insights", "contradictions", "hypotheses", "questions"}
    assert all(c == {"now": _NOW, "timezone_name": "UTC"} for c in calls.values())


@pytest.mark.asyncio
async def test_run_learning_step_isolates_a_failing_pass(monkeypatch):
    user_id = await _create_user(email="isolate@example.com")
    calls = _patch_steps(
        monkeypatch,
        insights=1,
        contradictions=RuntimeError("boom"),
        hypotheses=3,
        questions=4,
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        result = await learning_pipeline.run_learning_step(db, user, now=_NOW)

    assert result.contradictions == 0
    assert result.failed_steps == ["contradictions"]
    # A failure in one pass does not abort the passes after it.
    assert result.observations == 1
    assert result.hypotheses == 3
    assert result.open_questions == 4
    assert "hypotheses" in calls and "questions" in calls
    assert result.changed is True


@pytest.mark.asyncio
async def test_run_learning_step_skips_non_onboarded(monkeypatch):
    user_id = await _create_user(email="fresh@example.com", onboarded=False)
    calls = _patch_steps(monkeypatch, insights=1)

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        result = await learning_pipeline.run_learning_step(db, user, now=_NOW)

    assert result.changed is False
    assert result.failed_steps == []
    assert calls == {}  # no learning pass is run for a non-onboarded athlete


@pytest.mark.asyncio
async def test_learn_from_completed_workouts_disabled_returns_none(monkeypatch):
    user_id = await _create_user(email="off@example.com")
    calls = _patch_steps(monkeypatch, insights=1)
    monkeypatch.setattr(
        learning_pipeline.settings, "continuous_learning_enabled", False
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        result = await learning_pipeline.learn_from_completed_workouts(db, user)

    assert result is None
    assert calls == {}  # the flag short-circuits before any pass runs


@pytest.mark.asyncio
async def test_learn_from_completed_workouts_enabled_runs(monkeypatch):
    user_id = await _create_user(email="on@example.com")
    _patch_steps(monkeypatch, insights=2)
    monkeypatch.setattr(
        learning_pipeline.settings, "continuous_learning_enabled", True
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        result = await learning_pipeline.learn_from_completed_workouts(
            db, user, timezone_name="UTC"
        )

    assert result is not None
    assert result.observations == 2
