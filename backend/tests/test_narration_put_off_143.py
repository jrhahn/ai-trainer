"""A message about an automated run never says a session was put off that the
plan still trains in full (weekly review of 2026-11-02, ai-trainer-ops#143).

The nightly run of 2026-10-29 said the next day's session was postponed so a
sore throat could heal, and kept the full 75 to 105 minute ride at 175 to 215 W.
The check of #316 read only a day called free; "verschoben" passed it.
"""

from __future__ import annotations

from datetime import date

import pytest

import crud
from services import coach_summary
from services.coach_summary import days_called_put_off
from tests.conftest import TestSessionLocal
from tests.test_coach_summary import _create_user, _day, _mock_summary, _record_batch
from tests.test_narration_claims_316 import _next

FRIDAY = date(2026, 10, 23)
THURSDAY = "2026-10-29"
SATURDAY = "2026-10-24"


@pytest.mark.parametrize(
    "text",
    [
        "Die Einheit am Donnerstag habe ich verschoben, damit dein Hals heilt.",
        "Donnerstag fällt aus.",
        "Die Intervalle am Donnerstag sind gestrichen.",
        "I postponed Thursday's ride so your throat can heal.",
        "I cancelled the ride on Thursday.",
        "Thursday's session is postponed.",
        "Thursday’s session is called off.",
    ],
)
def test_a_session_said_to_be_put_off_is_read_as_one(text):
    assert days_called_put_off(text, FRIDAY) == [THURSDAY]


@pytest.mark.parametrize(
    "text",
    [
        # Where a session went, not one that went away.
        "Die Einheit habe ich auf Donnerstag verschoben.",
        "I postponed the ride to Thursday.",
        "The intervals are pushed back until Thursday.",
        "Donnerstag habe ich nicht verschoben.",
        "Nothing on Thursday was cancelled.",
        # Another clause, about another session.
        "Donnerstag fährst du locker, die Intervalle habe ich verschoben.",
        "I moved Thursday's ride.",
    ],
)
def test_other_sentences_put_no_session_off(text):
    assert days_called_put_off(text, FRIDAY) == []


def test_a_move_from_one_day_to_another_puts_off_the_first_only():
    text = "Die Einheit vom Donnerstag habe ich verschoben, auf Samstag."
    assert days_called_put_off(text, FRIDAY) == [THURSDAY]


def test_a_list_of_days_put_off_is_read_whole():
    text = "Samstag und Donnerstag habe ich verschoben."
    assert days_called_put_off(text, FRIDAY) == [SATURDAY, THURSDAY]


SAID = "Die Einheit am Donnerstag habe ich verschoben, damit dein Hals in Ruhe heilt."


async def _narrate(email: str, monkeypatch, old: dict, new: dict, applied: bool):
    user_id = await _create_user(email)
    monday = _next(0)
    changes = [
        {
            "date": monday,
            "old_day": _day(monday),
            "new_day": _day(monday, "recovery", 45),
            "applied": True,
        },
        {"date": new["date"], "old_day": old, "new_day": new, "applied": applied},
    ]
    batch_id = await _record_batch(user_id, "nightly_maintenance", changes)
    _mock_summary(monkeypatch, {"summary": SAID, "days": []})
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        row = await coach_summary.narrate_plan_changes(
            db,
            user,
            batch_id=batch_id,
            source="nightly_maintenance",
            applied_changes=[c for c in changes if c["applied"]],
            plan=[_day(monday, "recovery", 45), new if applied else old],
        )
        await db.commit()
        messages = await crud.get_chat_messages(db, user_id)
    return row, messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "old", "new", "applied"),
    [
        # Rewritten, but as long and of the same kind: the 2026-10-29 run.
        ("kept in full", ("endurance", 105), ("endurance", 105), True),
        ("made longer", ("endurance", 75), ("endurance", 105), True),
        # The run did not touch the day at all.
        ("untouched", ("endurance", 105), ("rest", 0), False),
    ],
)
async def test_a_message_putting_off_a_session_the_plan_keeps_is_not_posted(
    monkeypatch, label, old, new, applied
):
    thursday = _next(3)
    row, messages = await _narrate(
        f"put-off-143-{label.replace(' ', '-')}@example.com",
        monkeypatch,
        _day(thursday, *old),
        _day(thursday, *new),
        applied,
    )

    assert row is None
    assert messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "new"),
    [
        ("now rest", ("rest", 0)),
        ("shorter", ("endurance", 45)),
        ("another kind", ("recovery", 105)),
    ],
)
async def test_the_message_is_posted_when_the_session_was_eased(
    monkeypatch, label, new
):
    thursday = _next(3)
    row, messages = await _narrate(
        f"put-off-143-eased-{label.replace(' ', '-')}@example.com",
        monkeypatch,
        _day(thursday, "endurance", 105),
        _day(thursday, *new),
        True,
    )

    assert row is not None
    assert [m.content for m in messages] == [SAID]


@pytest.mark.asyncio
async def test_the_message_is_posted_for_a_day_that_was_free_already(monkeypatch):
    thursday = _next(3)
    row, messages = await _narrate(
        "put-off-143-free-already@example.com",
        monkeypatch,
        _day(thursday, "rest", 0),
        _day(thursday, "rest", 0),
        False,
    )

    assert row is not None
    assert [m.content for m in messages] == [SAID]
