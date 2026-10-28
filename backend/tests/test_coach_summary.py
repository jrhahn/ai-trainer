"""Tests for the coach-run narrator (#439)."""

from __future__ import annotations

from functools import partial

import pytest
from sqlalchemy import select

import crud
import models
from services import coach_summary
from tests.conftest import TestSessionLocal
from tests.factories import create_user_row


def _day(date: str, workout: str = "endurance", duration: int = 60) -> dict:
    return {
        "date": date,
        "workoutType": workout,
        "title": f"{workout} {date}",
        "durationMinutes": duration,
    }


# The users row this module tests with; tests/factories.py writes it (#289).
_create_user = partial(
    create_user_row,
    name="Test Rider",
    is_onboarded=True,
    current_ftp=250,
    ai_provider="gemini",
)


async def _record_batch(user_id: str, source: str, changes: list[dict]) -> str:
    """Write plan_day_history rows and return their shared batch_id."""
    async with TestSessionLocal() as db:
        rows = await crud.record_plan_day_changes(db, user_id, changes, source)
        await db.commit()
        return rows[0].batch_id


def _mock_summary(monkeypatch, payload):
    async def fake_summarize(*args, **kwargs):
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(
        coach_summary.ai_service, "summarize_plan_changes", fake_summarize
    )


@pytest.mark.asyncio
async def test_narrates_run_writes_summary_reasons_and_message(monkeypatch):
    user_id = await _create_user("narrate@example.com")
    changes = [
        {
            "date": "2026-07-26",
            "old_day": _day("2026-07-26"),
            "new_day": _day("2026-07-26", "vo2max", 75),
            "applied": True,
        },
        {
            "date": "2026-07-24",
            "old_day": _day("2026-07-24"),
            "new_day": _day("2026-07-24", "recovery", 45),
            "applied": True,
        },
    ]
    batch_id = await _record_batch(user_id, "nightly_maintenance", changes)
    _mock_summary(
        monkeypatch,
        {
            "summary": "As a daily routine I switched Sunday to VO2max to lift your FTP.",
            "days": [
                {"date": "2026-07-26", "reason": "Endurance→VO2max; TSB positive"},
                {"date": "2026-07-24", "reason": "Kept easy to protect the weekend"},
            ],
        },
    )
    applied = [c for c in changes if c["applied"]]

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        row = await coach_summary.narrate_plan_changes(
            db,
            user,
            batch_id=batch_id,
            source="nightly_maintenance",
            applied_changes=applied,
            plan=[],
        )
        await db.commit()

    assert row is not None
    async with TestSessionLocal() as db:
        summaries = list(
            await db.scalars(
                select(models.PlanChangeSummary).where(
                    models.PlanChangeSummary.user_id == user_id
                )
            )
        )
        assert len(summaries) == 1
        assert summaries[0].batch_id == batch_id
        assert "VO2max" in summaries[0].summary

        messages = await crud.get_chat_messages(db, user_id)
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].content == summaries[0].summary
        assert messages[0].plan_update_count == 2

        history = await crud.list_plan_day_history(db, user_id)
        reasons = {h.date: h.reason for h in history}
        assert reasons["2026-07-26"] == "Endurance→VO2max; TSB positive"
        assert reasons["2026-07-24"] == "Kept easy to protect the weekend"

        narrated = await crud.get_narrated_batch_ids(db, user_id, [batch_id])
        assert narrated == {batch_id}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,batch_id,applied",
    [
        ("generate", "b1", [{"date": "2026-07-26"}]),  # not a narrated trigger
        ("nightly_maintenance", None, [{"date": "2026-07-26"}]),  # no batch recorded
        ("nightly_maintenance", "b1", []),  # blocked-only run: nothing applied
    ],
)
async def test_no_op_gates(monkeypatch, source, batch_id, applied):
    user_id = await _create_user(f"gate-{source}-{batch_id}-{len(applied)}@example.com")
    called = {"n": 0}

    async def fake_summarize(*args, **kwargs):
        called["n"] += 1
        return {"summary": "x", "days": []}

    monkeypatch.setattr(
        coach_summary.ai_service, "summarize_plan_changes", fake_summarize
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        row = await coach_summary.narrate_plan_changes(
            db,
            user,
            batch_id=batch_id,
            source=source,
            applied_changes=applied,
            plan=[],
        )
        await db.commit()

    assert row is None
    assert called["n"] == 0
    async with TestSessionLocal() as db:
        assert await crud.get_chat_messages(db, user_id) == []


@pytest.mark.asyncio
async def test_llm_failure_is_safe(monkeypatch):
    user_id = await _create_user("fail@example.com")
    changes = [
        {
            "date": "2026-07-26",
            "old_day": None,
            "new_day": _day("2026-07-26"),
            "applied": True,
        }
    ]
    batch_id = await _record_batch(user_id, "ride_review", changes)
    _mock_summary(monkeypatch, RuntimeError("LLM down"))

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        row = await coach_summary.narrate_plan_changes(
            db,
            user,
            batch_id=batch_id,
            source="ride_review",
            applied_changes=changes,
            plan=[],
        )
        await db.commit()

    assert row is None
    async with TestSessionLocal() as db:
        assert await crud.get_chat_messages(db, user_id) == []
        assert await crud.get_narrated_batch_ids(db, user_id, [batch_id]) == set()


@pytest.mark.asyncio
async def test_empty_summary_persists_nothing(monkeypatch):
    user_id = await _create_user("empty@example.com")
    changes = [
        {
            "date": "2026-07-26",
            "old_day": None,
            "new_day": _day("2026-07-26"),
            "applied": True,
        }
    ]
    batch_id = await _record_batch(user_id, "auto_adapt", changes)
    _mock_summary(monkeypatch, {"summary": "   ", "days": []})

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        row = await coach_summary.narrate_plan_changes(
            db,
            user,
            batch_id=batch_id,
            source="auto_adapt",
            applied_changes=changes,
            plan=[],
        )
        await db.commit()

    assert row is None
    async with TestSessionLocal() as db:
        assert await crud.get_chat_messages(db, user_id) == []
