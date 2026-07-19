"""Tests for the source-aware duration refresh (#427/#429).

Covers the intervals float-tolerant id join (Bug B), the recent-window ``since``
filter used by the daily job (Bug A), and that the one-off backfill CLI still
imports and delegates here.
"""

import importlib
from datetime import date, datetime, timezone
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


# --- Strava list fetch -------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal stand-in for ``httpx.AsyncClient`` as an async context manager."""

    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, params, headers):
        self.calls.append(params)
        page = params["page"]  # 1-based
        payload = self._pages[page - 1] if page - 1 < len(self._pages) else []
        return _FakeResponse(payload)


@pytest.mark.asyncio
async def test_fetch_strava_moving_times_paginates_and_bounds(monkeypatch):
    full = [
        {"id": i, "moving_time": 100 + i}
        for i in range(duration_refresh.STRAVA_LIST_PER_PAGE)
    ]
    tail = [{"id": 9001, "moving_time": 4225}, {"id": 9002, "moving_time": 0}]
    fake = _FakeClient([full, tail])
    monkeypatch.setattr(duration_refresh.httpx, "AsyncClient", lambda *a, **k: fake)

    async def _no_sleep(_):  # don't actually pause between pages
        return None

    monkeypatch.setattr(duration_refresh.asyncio, "sleep", _no_sleep)

    after = datetime(2026, 7, 1, tzinfo=timezone.utc)
    result = await duration_refresh.fetch_strava_moving_times("tok", after=after)

    # First page full → keep paging; second short → stop. Two calls total.
    assert len(fake.calls) == 2
    assert fake.calls[0]["after"] == int(after.timestamp())
    assert result[0] == 100
    assert result[9001] == 4225
    assert result[9002] == 0  # zero kept here; filtered later in corrected_durations


@pytest.mark.asyncio
async def test_fetch_strava_moving_times_raises_on_error(monkeypatch):
    class _ErrClient(_FakeClient):
        async def get(self, url, *, params, headers):
            return _FakeResponse(None, status_code=429)

    monkeypatch.setattr(
        duration_refresh.httpx, "AsyncClient", lambda *a, **k: _ErrClient([])
    )
    with pytest.raises(RuntimeError, match="Strava list error 429"):
        await duration_refresh.fetch_strava_moving_times("tok")


# --- Strava correction branch ------------------------------------------------


def _strava_user():
    return SimpleNamespace(
        id="u2",
        email="s@b.com",
        strava_token=SimpleNamespace(),
        intervals_token=None,
    )


def _strava_metric(activity_id=555, duration=30491):
    return SimpleNamespace(
        activity_source="strava",
        activity_date="2026-07-18",
        activity_name="Strava ride",
        strava_activity_id=activity_id,
        duration_seconds=duration,
    )


@pytest.mark.asyncio
async def test_corrected_durations_matches_strava_by_exact_id(monkeypatch):
    metric = _strava_metric()
    captured = {}

    async def fake_metrics(db, user_id):
        return [metric]

    async def fake_token(token, db):
        return "access-tok"

    async def fake_fetch(access_token, *, after=None):
        captured["after"] = after
        captured["token"] = access_token
        return {555: 15914, 777: 999}

    monkeypatch.setattr(
        duration_refresh.crud, "get_all_ride_metrics_ordered", fake_metrics
    )
    monkeypatch.setattr(duration_refresh, "ensure_fresh_strava_token", fake_token)
    monkeypatch.setattr(duration_refresh, "fetch_strava_moving_times", fake_fetch)

    changes = await duration_refresh.corrected_durations(
        None, _strava_user(), since=date(2026, 7, 1)
    )

    assert changes == [(metric, 30491, 15914)]
    assert captured["token"] == "access-tok"
    # A bounded window passes an ``after`` earlier than ``since`` by the padding.
    assert captured["after"] is not None
    assert captured["after"] < datetime(2026, 7, 1, tzinfo=timezone.utc)


# --- refresh_user_durations --------------------------------------------------


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_refresh_user_durations_no_token_short_circuits():
    user = SimpleNamespace(strava_token=None, intervals_token=None)
    assert await duration_refresh.refresh_user_durations(
        _FakeDB(), user, apply=True
    ) == (0, 0)


@pytest.mark.asyncio
async def test_refresh_user_durations_applies_and_recalculates(monkeypatch):
    metric = _corrupted_intervals_metric()
    db = _FakeDB()

    async def fake_corrected(db_, user, *, since=None):
        return [(metric, 30491, 15914)]

    async def fake_recalc(db_, user):
        return 7, None

    monkeypatch.setattr(duration_refresh, "corrected_durations", fake_corrected)
    monkeypatch.setattr(
        duration_refresh.metrics_service, "recalculate_metrics_for_user", fake_recalc
    )

    changed, recalculated = await duration_refresh.refresh_user_durations(
        db, _intervals_user(), apply=True
    )

    assert (changed, recalculated) == (1, 7)
    assert metric.duration_seconds == 15914  # mutated in place
    assert db.commits == 1


@pytest.mark.asyncio
async def test_refresh_user_durations_dry_run_does_not_mutate(monkeypatch):
    metric = _corrupted_intervals_metric()
    original = metric.duration_seconds
    db = _FakeDB()

    async def fake_corrected(db_, user, *, since=None):
        return [(metric, original, 15914)]

    async def boom(*a, **k):  # pragma: no cover - must not run on a dry run
        raise AssertionError("recalculate must not run without apply")

    monkeypatch.setattr(duration_refresh, "corrected_durations", fake_corrected)
    monkeypatch.setattr(
        duration_refresh.metrics_service, "recalculate_metrics_for_user", boom
    )

    changed, recalculated = await duration_refresh.refresh_user_durations(
        db, _intervals_user(), apply=False
    )

    assert (changed, recalculated) == (1, 0)
    assert metric.duration_seconds == original  # untouched
    assert db.commits == 1  # still commits to release the refreshed token/lock


@pytest.mark.asyncio
async def test_refresh_user_durations_survives_missing_ftp(monkeypatch):
    metric = _corrupted_intervals_metric()
    db = _FakeDB()

    async def fake_corrected(db_, user, *, since=None):
        return [(metric, 30491, 15914)]

    async def no_ftp(db_, user):
        raise ValueError("no FTP configured")

    monkeypatch.setattr(duration_refresh, "corrected_durations", fake_corrected)
    monkeypatch.setattr(
        duration_refresh.metrics_service, "recalculate_metrics_for_user", no_ftp
    )

    changed, recalculated = await duration_refresh.refresh_user_durations(
        db, _intervals_user(), apply=True
    )

    # Duration is still fixed; the chain is left as-is rather than raising.
    assert (changed, recalculated) == (1, 0)
    assert metric.duration_seconds == 15914
    assert db.commits == 1


# --- run_duration_refresh (daily job) ----------------------------------------


class _SessionFactory:
    def __init__(self, users):
        self._users = users
        self.db = _FakeDB()

        async def scalars(query):
            return list(self._users)

        self.db.scalars = scalars

    def __call__(self):
        factory = self

        class _Ctx:
            async def __aenter__(self_inner):
                return factory.db

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_run_duration_refresh_aggregates_and_skips_unconnected(monkeypatch):
    connected = _intervals_user()
    unconnected = SimpleNamespace(
        id="u3", email="none@b.com", strava_token=None, intervals_token=None
    )
    factory = _SessionFactory([connected, unconnected])

    async def fake_refresh(db, user, *, apply, since):
        assert apply is True
        assert since is not None  # daily job always bounds the window
        return 2, 5

    monkeypatch.setattr(duration_refresh, "refresh_user_durations", fake_refresh)

    result = await duration_refresh.run_duration_refresh(factory, lookback_days=21)

    # Only the connected athlete is counted.
    assert result.users == 1
    assert result.changed == 2
    assert result.recalculated == 5
    assert result.failed == 0


@pytest.mark.asyncio
async def test_run_duration_refresh_counts_failures(monkeypatch):
    factory = _SessionFactory([_intervals_user()])

    async def boom(db, user, *, apply, since):
        raise RuntimeError("provider down")

    monkeypatch.setattr(duration_refresh, "refresh_user_durations", boom)

    result = await duration_refresh.run_duration_refresh(factory, lookback_days=21)

    assert result.failed == 1
    assert result.users == 0
    assert factory.db.rollbacks == 1


def test_duration_refresh_job_is_scheduled_daily():
    job = duration_refresh.duration_refresh_job(session_factory=lambda: None)
    assert job.name == "duration-refresh"
    delay = job.next_delay()
    assert isinstance(delay, (int, float)) and delay > 0
