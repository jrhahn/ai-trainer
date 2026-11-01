"""The nightly duration refresh goes on after an athlete fails (review of 2026-11-01, ai-trainer-ops#338, C4).

The job kept every athlete in one session and rolled it back on a failure;
the rollback expired them all, and the next attribute read outside an await
raised in the async session, which ended the job for everyone after.
"""

from __future__ import annotations

import pytest

import models
from services import duration_refresh
from tests.conftest import TestSessionLocal
from tests.factories import create_user_row


async def _connected(email: str) -> str:
    user_id = await create_user_row(email, [], is_onboarded=True)
    async with TestSessionLocal() as db:
        db.add(
            models.IntervalsToken(
                user_id=user_id,
                api_key="intervals-key",
                athlete_id="i1",
                athlete_name="R",
            )
        )
        await db.commit()
    return user_id


@pytest.mark.asyncio
async def test_an_athlete_that_fails_does_not_stop_the_others(monkeypatch):
    first = await _connected("refresh-first@example.com")
    second = await _connected("refresh-second@example.com")
    seen: list[str] = []

    async def refresh(db, user, *, apply, since):
        seen.append(user.id)
        if user.id == first:
            raise RuntimeError("provider down")
        return 1, 2

    monkeypatch.setattr(duration_refresh, "refresh_user_durations", refresh)

    result = await duration_refresh.run_duration_refresh(
        TestSessionLocal, lookback_days=21
    )

    assert set(seen) == {first, second}
    assert result.failed == 1
    assert result.users == 1


@pytest.mark.asyncio
async def test_only_connected_athletes_are_refreshed_and_counted(monkeypatch):
    connected = await _connected("refresh-connected@example.com")
    await create_user_row("refresh-unconnected@example.com", [], is_onboarded=True)
    seen: list[str] = []

    async def refresh(db, user, *, apply, since):
        assert apply is True
        assert since is not None  # the daily job always bounds the window
        seen.append(user.id)
        return 2, 5

    monkeypatch.setattr(duration_refresh, "refresh_user_durations", refresh)

    result = await duration_refresh.run_duration_refresh(
        TestSessionLocal, lookback_days=21
    )

    assert seen == [connected]
    assert (result.users, result.changed, result.recalculated, result.failed) == (
        1,
        2,
        5,
        0,
    )


@pytest.mark.asyncio
async def test_an_athlete_with_nothing_changed_is_not_counted(monkeypatch):
    await _connected("refresh-unchanged@example.com")

    async def refresh(db, user, *, apply, since):
        return 0, 0

    monkeypatch.setattr(duration_refresh, "refresh_user_durations", refresh)

    result = await duration_refresh.run_duration_refresh(
        TestSessionLocal, lookback_days=21
    )

    assert (result.users, result.failed) == (0, 0)
