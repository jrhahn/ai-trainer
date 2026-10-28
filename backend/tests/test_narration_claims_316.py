"""A message about an automated run never calls a day free that the plan still
trains (ai-trainer-ops#316).

The nightly run of 2026-10-23 said a break was planned for Thursday, as the
athlete wanted, while the plan kept a 45-minute strength session there. The
narrator writes from the applied changes, but nothing checked what it then
said, and its per-day reasons were written onto blocked rows too.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

import crud
import models
from services import coach_summary
from services.coach_summary import days_called_free
from services.dates import app_today
from tests.conftest import TestSessionLocal
from tests.test_coach_summary import _create_user, _day, _mock_summary, _record_batch

FRIDAY = date(2026, 10, 23)
THURSDAY = "2026-10-29"


@pytest.mark.parametrize(
    "text",
    [
        "Wie gewünscht ist am Donnerstag eine Pause eingeplant.",
        "Donnerstag ist jetzt frei.",
        "Am Donnerstag hast du frei.",
        "Ruhetag am Donnerstag, dafür fährst du Freitag länger.",
        "Thursday is now a rest day.",
        "I gave you a day off on Thursday.",
        # A week written as a list (review of #1212).
        "Donnerstag: Ruhetag",
        "Thursday: rest day",
        "Donnerstag – frei",
    ],
)
def test_a_day_called_free_is_read_as_one(text):
    assert days_called_free(text, FRIDAY) == [THURSDAY]


@pytest.mark.parametrize(
    "text",
    [
        "Donnerstag machst du eine kurze Pause zwischen den Intervallen.",
        "Donnerstag eine kurze Pause zwischen den Intervallen.",
        "Thursday the rest of the set stays.",
        "Donnerstag ist nicht frei, da fährst du Intervalle.",
        "Keine Pause am Donnerstag.",
        "Donnerstag und Freitag fährst du frei nach Gefühl.",
    ],
)
def test_other_sentences_call_no_day_free(text):
    assert days_called_free(text, FRIDAY) == []


def _next(weekday: int) -> str:
    today = app_today()
    return (today + timedelta(days=(weekday - today.weekday()) % 7)).isoformat()


SAID = "Wie gewünscht ist am Donnerstag eine Pause eingeplant, Montag fährst du locker."


async def _narrate(email: str, monkeypatch, thursday: dict) -> tuple[str, object]:
    user_id = await _create_user(email)
    monday = _next(0)
    changes = [
        {
            "date": monday,
            "old_day": _day(monday),
            "new_day": _day(monday, "recovery", 45),
            "applied": True,
        },
        # The writer's Thursday, which a guard kept as it was.
        {
            "date": thursday["date"],
            "old_day": _day(thursday["date"], "strength", 45),
            "new_day": _day(thursday["date"], "rest", 0),
            "applied": False,
        },
    ]
    batch_id = await _record_batch(user_id, "nightly_maintenance", changes)
    _mock_summary(
        monkeypatch,
        {
            "summary": SAID,
            "days": [
                {"date": monday, "reason": "Easy after the weekend"},
                {"date": thursday["date"], "reason": "As you asked"},
            ],
        },
    )
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        row = await coach_summary.narrate_plan_changes(
            db,
            user,
            batch_id=batch_id,
            source="nightly_maintenance",
            applied_changes=[c for c in changes if c["applied"]],
            plan=[_day(monday, "recovery", 45), thursday],
        )
        await db.commit()
    return user_id, row


@pytest.mark.asyncio
async def test_a_message_calling_a_trained_day_free_is_not_posted(monkeypatch):
    user_id, row = await _narrate(
        "narrate-316a@example.com", monkeypatch, _day(_next(3), "strength", 45)
    )

    assert row is None
    async with TestSessionLocal() as db:
        assert await crud.get_chat_messages(db, user_id) == []
        summaries = await db.scalars(
            select(models.PlanChangeSummary).where(
                models.PlanChangeSummary.user_id == user_id
            )
        )
        assert list(summaries) == []


@pytest.mark.asyncio
async def test_the_message_is_posted_when_the_day_is_free(monkeypatch):
    user_id, row = await _narrate(
        "narrate-316b@example.com", monkeypatch, _day(_next(3), "rest", 0)
    )

    assert row is not None
    async with TestSessionLocal() as db:
        [message] = await crud.get_chat_messages(db, user_id)
        assert message.content == SAID
        # A reason only for the day the run changed, not for the blocked one.
        reasons = {
            h.date: h.reason for h in await crud.list_plan_day_history(db, user_id)
        }
        assert reasons == {_next(0): "Easy after the weekend", _next(3): None}


def test_a_weekday_said_on_that_day_is_today():
    assert days_called_free("Donnerstag ist frei.", date(2026, 10, 29)) == [THURSDAY]


@pytest.mark.parametrize(
    "text",
    [
        "Donnerstag, Freitag und Samstag sind frei.",
        "Thursday, Friday and Saturday are rest days.",
    ],
)
def test_a_list_of_days_called_free_is_read_whole(text):
    assert days_called_free(text, FRIDAY) == ["2026-10-23", "2026-10-24", THURSDAY]
