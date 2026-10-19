"""Uploaded past rides get the load picture of their own day (ai-trainer-ops#156).

Each uploaded file used to be chained on its own, seeded from the newest stored
row as if it came the day after it. An export of past months arrives in any
order and is older than what is stored, so every ride got another day's
fitness and fatigue, and the newest row never counted them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import models
from services import metrics_service
from services.fitness_ledger import LoadLedger
from tests.conftest import TestSessionLocal

BULK = "/api/v1/users/me/upload-fit/bulk"
SINGLE = "/api/v1/users/me/upload-fit"
START = datetime(2026, 3, 1, 8, tzinfo=timezone.utc)


class _Field:
    def __init__(self, name, value):
        self.name, self.value = name, value


class _Record:
    def __init__(self, fields):
        self._fields = fields

    def __iter__(self):
        return iter(self._fields)


class _FitFile:
    """Stands in for fitparse. The bytes say ``<day offset>:<minutes>``."""

    def __init__(self, raw_stream, *args, **kwargs):
        day, minutes = raw_stream.read().decode().split(":")
        self.start = START + timedelta(days=int(day))
        self.seconds = int(minutes) * 60
        self.serial = f"{day}-{minutes}"

    def parse(self):
        return None

    def get_messages(self, msg_type):
        if msg_type == "file_id":
            return [_Record([_Field("serial_number", self.serial), _Field("time_created", self.start)])]
        if msg_type == "session":
            return [
                _Record(
                    [
                        _Field("sport", "cycling"),
                        _Field("total_elapsed_time", self.seconds),
                        _Field("start_time", self.start),
                        _Field("avg_heart_rate", 140),
                    ]
                )
            ]
        return []


@pytest.fixture(autouse=True)
def fake_fitparse(monkeypatch, mock_ai_service):
    import fitparse

    monkeypatch.setattr(fitparse, "FitFile", _FitFile)


async def _rows() -> list[models.RideMetric]:
    async with TestSessionLocal() as db:
        return list(
            (
                await db.scalars(
                    select(models.RideMetric).order_by(
                        models.RideMetric.activity_date, models.RideMetric.activity_start_datetime
                    )
                )
            ).all()
        )


def _expected_chain(rows: list[models.RideMetric]) -> list[tuple[float, float]]:
    """CTL/ATL over the stored loads, from zero, in date order."""
    ledger, previous, chain = LoadLedger(), None, []
    for row in rows:
        gap = 1
        if previous is not None:
            gap = max(0, (datetime.fromisoformat(row.activity_date) - datetime.fromisoformat(previous)).days)
        ledger = ledger.advance(sport="cycling", load=row.tss or 0.0, days_since_previous=gap)
        previous = row.activity_date
        chain.append((round(ledger.ctl("cycling"), 2), round(ledger.atl, 2)))
    return chain


async def _set_profile(client, headers):
    await client.put("/api/v1/users/me", json={"maxHeartRate": 190, "currentFTP": 250}, headers=headers)


@pytest.mark.asyncio
async def test_a_batch_out_of_date_order_is_chained_by_date(client: AsyncClient, auth_headers):
    await _set_profile(client, auth_headers)
    files = [("c.fit", b"40:90"), ("a.fit", b"0:60"), ("b.fit", b"3:120")]

    response = await client.post(
        BULK,
        headers=auth_headers,
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
    )

    assert response.json()["imported"] == 3
    rows = await _rows()
    assert [r.activity_date for r in rows] == ["2026-03-01", "2026-03-04", "2026-04-10"]
    assert all(r.tss for r in rows), "the test needs a load on every ride"
    assert [(r.ctl_after, r.atl_after) for r in rows] == _expected_chain(rows)


@pytest.mark.asyncio
async def test_an_older_ride_uploaded_later_moves_the_newer_rows(client: AsyncClient, auth_headers):
    await _set_profile(client, auth_headers)
    for data in (b"30:60", b"2:180"):
        response = await client.post(
            SINGLE, headers=auth_headers, files={"file": ("ride.fit", data, "application/octet-stream")}
        )
        assert response.status_code == 200

    rows = await _rows()
    assert [r.activity_date for r in rows] == ["2026-03-03", "2026-03-31"]
    assert [(r.ctl_after, r.atl_after) for r in rows] == _expected_chain(rows)
    # The newest row now carries the older ride's load, decayed over 28 days.
    assert rows[1].ctl_after > _expected_chain(rows[1:])[0][0]


@pytest.mark.asyncio
async def test_a_batch_that_imports_nothing_replays_nothing(client: AsyncClient, auth_headers, monkeypatch):
    calls = []
    monkeypatch.setattr(metrics_service, "replay_load_chain", lambda rows: calls.append(rows))

    response = await client.post(
        BULK, headers=auth_headers, files=[("files", ("x.gpx", b"<gpx/>", "application/octet-stream"))]
    )

    assert response.json()["imported"] == 0
    assert calls == []


@pytest.mark.asyncio
async def test_two_rides_on_one_day_replay_in_start_order(client: AsyncClient, auth_headers, monkeypatch):
    """The ordered read breaks a same-day tie by start time (review of app PR #970)."""
    import crud

    await _set_profile(client, auth_headers)
    await client.post(BULK, headers=auth_headers, files=[("files", ("a.fit", b"0:60", "application/octet-stream"))])
    async with TestSessionLocal() as db:
        [first] = list(await db.scalars(select(models.RideMetric)))
        # Stored after the 08:00 ride but started before it.
        dawn = models.RideMetric(
            user_id=first.user_id,
            strava_activity_id=first.strava_activity_id + 1,
            activity_date=first.activity_date,
            activity_start_datetime=f"{first.activity_date}T05:00:00+00:00",
            sport_type="cycling",
            duration_seconds=1800,
            tss=40.0,
        )
        db.add(dawn)
        await db.commit()

        ordered = await crud.get_all_ride_metrics_ordered(db, first.user_id)

    assert [m.activity_start_datetime[11:16] for m in ordered] == ["05:00", "08:00"]
