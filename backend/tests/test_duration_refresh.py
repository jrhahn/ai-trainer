"""Tests for the source-aware duration refresh (#427/#429).

Covers the intervals float-tolerant id join (Bug B), the recent-window ``since``
filter used by the daily job (Bug A), and that the one-off backfill CLI still
imports and delegates here.
"""

import importlib
from datetime import date
from types import SimpleNamespace

import pytest

from services import duration_refresh
from services.intervals_service import intervals_activity_id

# The backfill CLI is a thin wrapper around this module; keep it importable.
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

    monkeypatch.setattr(duration_refresh, "fetch_recent_activities", fake_fetch)

    result = await duration_refresh.fetch_intervals_moving_times(
        "key", "0", oldest=date(2026, 1, 1), newest=date(2026, 12, 31)
    )

    assert result == {float(intervals_activity_id("i166933341")): 15914}


def test_within_window_filters_by_since():
    ride = SimpleNamespace(activity_date="2026-07-18")
    assert duration_refresh._within_window(ride, None) is True
    assert duration_refresh._within_window(ride, date(2026, 7, 1)) is True
    assert duration_refresh._within_window(ride, date(2026, 7, 18)) is True
    assert duration_refresh._within_window(ride, date(2026, 7, 19)) is False
    # A ride without a date is excluded from a bounded window.
    no_date = SimpleNamespace(activity_date="")
    assert duration_refresh._within_window(no_date, date(2026, 7, 1)) is False


def _intervals_user():
    return SimpleNamespace(
        id="u1",
        email="a@b.com",
        strava_token=None,
        intervals_token=SimpleNamespace(api_key="key", athlete_id="0"),
    )


def _corrupted_intervals_metric():
    """A ride stored with the float64-corrupted id and the elapsed duration."""
    return SimpleNamespace(
        activity_source="intervals",
        activity_date="2026-07-18",
        activity_name="Long ride with a coffee stop",
        strava_activity_id=1241310107630749000,  # corrupted render of the hash
        duration_seconds=30491,  # elapsed_time, wrong
    )


@pytest.mark.asyncio
async def test_corrected_durations_matches_intervals_via_float(monkeypatch):
    metric = _corrupted_intervals_metric()

    async def fake_metrics(db, user_id):
        return [metric]

    async def fake_fetch(api_key, athlete_id, *, oldest, newest):
        return [{"id": "i166933341", "moving_time": 15914}]

    monkeypatch.setattr(
        duration_refresh.crud, "get_all_ride_metrics_ordered", fake_metrics
    )
    monkeypatch.setattr(duration_refresh, "fetch_recent_activities", fake_fetch)

    changes = await duration_refresh.corrected_durations(None, _intervals_user())

    assert changes == [(metric, 30491, 15914)]


@pytest.mark.asyncio
async def test_corrected_durations_since_excludes_old_rides(monkeypatch):
    metric = _corrupted_intervals_metric()

    async def fake_metrics(db, user_id):
        return [metric]

    async def fail_fetch(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("intervals should not be fetched for a filtered-out ride")

    monkeypatch.setattr(
        duration_refresh.crud, "get_all_ride_metrics_ordered", fake_metrics
    )
    monkeypatch.setattr(duration_refresh, "fetch_recent_activities", fail_fetch)

    changes = await duration_refresh.corrected_durations(
        None, _intervals_user(), since=date(2026, 7, 19)
    )

    assert changes == []


def test_backfill_cli_delegates_to_service():
    # The CLI wrapper imports the shared correction rather than reimplementing it.
    assert backfill.refresh_user_durations is duration_refresh.refresh_user_durations
