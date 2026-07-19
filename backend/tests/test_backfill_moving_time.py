"""Tests for the moving_time backfill script's intervals matching (#427/#429)."""

import importlib

import pytest

from services.intervals_service import intervals_activity_id

backfill = importlib.import_module("scripts.backfill_activity_moving_time")


def test_intervals_id_float_join_survives_precision_loss():
    """A float64-corrupted stored id still matches the true hash via float().

    intervals ids round-trip through the frontend as JS Numbers, so a 19-digit
    hash is stored as its float64 decimal rendering (e.g. ...749000) while the
    true int is ...748932. An exact int compare misses; float() matches because
    both map to the same float64 (#429 Bug B).
    """
    raw_id = "i166933341"
    true_hash = intervals_activity_id(raw_id)
    stored_corrupted = 1241310107630749000  # what the frontend actually stored

    assert true_hash != stored_corrupted  # exact int compare misses
    assert float(true_hash) == float(stored_corrupted)  # float join matches


@pytest.mark.asyncio
async def test_fetch_intervals_moving_times_keys_by_float(monkeypatch):
    async def fake_fetch(api_key, athlete_id, *, oldest, newest):
        return [
            {"id": "i166933341", "moving_time": 15914},
            {"id": "i222", "moving_time": 0},  # dropped: not > 0
            {"id": "i333"},  # dropped: no moving_time
        ]

    monkeypatch.setattr(backfill, "fetch_recent_activities", fake_fetch)

    from datetime import date

    result = await backfill._fetch_intervals_moving_times(
        "key", "0", oldest=date(2026, 1, 1), newest=date(2026, 12, 31)
    )

    assert result == {float(intervals_activity_id("i166933341")): 15914}
