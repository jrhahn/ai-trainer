"""An export of past months goes in whole (ai-trainer-ops#157).

Every uploaded file was analysed by the model and paid for it, so a history
export stopped after ten files on the burst limit. Only a recent ride gets the
model's read now; older ones count for load and fitness and cost nothing.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

import models
import routers.users as users_router
from config import settings
from services.dates import app_today
from tests.conftest import TestSessionLocal

BULK = "/api/v1/users/me/upload-fit/bulk"


class _Field:
    def __init__(self, name, value):
        self.name, self.value = name, value


class _Record:
    def __init__(self, fields):
        self._fields = fields

    def __iter__(self):
        return iter(self._fields)


class _FitFile:
    """Stands in for fitparse. The bytes are the ride's age in days."""

    def __init__(self, raw_stream, *args, **kwargs):
        age = int(raw_stream.read().decode())
        day = app_today() - timedelta(days=age)
        self.start = datetime.combine(day, time(8), tzinfo=timezone.utc)
        self.serial = f"ride-{age}"

    def parse(self):
        return None

    def get_messages(self, msg_type):
        if msg_type == "file_id":
            return [
                _Record(
                    [
                        _Field("serial_number", self.serial),
                        _Field("time_created", self.start),
                    ]
                )
            ]
        if msg_type == "session":
            return [
                _Record(
                    [
                        _Field("sport", "cycling"),
                        _Field("total_elapsed_time", 3600),
                        _Field("start_time", self.start),
                        _Field("avg_power", 180),
                    ]
                )
            ]
        return []


@pytest.fixture(autouse=True)
def fake_fitparse(monkeypatch):
    import fitparse

    monkeypatch.setattr(fitparse, "FitFile", _FitFile)


@pytest.fixture
def tight_limit(monkeypatch):
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 2)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)


async def _upload(client, headers, *ages: int) -> dict:
    response = await client.post(
        BULK,
        headers=headers,
        files=[
            ("files", (f"{age}.fit", str(age).encode(), "application/octet-stream"))
            for age in ages
        ],
    )
    assert response.status_code == 200
    return response.json()


async def _snapshots() -> int:
    async with TestSessionLocal() as db:
        return await db.scalar(
            select(func.count()).select_from(models.AthleteMetricSnapshot)
        )


@pytest.mark.asyncio
async def test_a_history_export_goes_in_whole_past_the_burst_limit(
    client: AsyncClient, auth_headers, mock_ai_service, tight_limit
):
    ages = list(range(20, 20 + settings.fit_upload_bulk_max_files))

    body = await _upload(client, auth_headers, *ages)

    assert body["imported"] == len(ages), body
    assert mock_ai_service["analyse_fit_activity"].await_count == 0
    # Nothing stamped as today's FTP from a ride months ago.
    assert await _snapshots() == 0


@pytest.mark.asyncio
async def test_recent_rides_are_still_read_and_paid_for(
    client: AsyncClient, auth_headers, mock_ai_service, tight_limit
):
    body = await _upload(
        client, auth_headers, 0, 60, 1, 90, users_router.FIT_ANALYSIS_RECENT_DAYS, 2
    )

    statuses = {f["filename"]: f["status"] for f in body["files"]}
    # Burst of two: the request paid for the first recent ride, the second paid
    # its own way, the third recent one found the allowance spent. The old
    # rides in between never asked.
    assert statuses["0.fit"] == statuses["1.fit"] == "imported"
    assert statuses["60.fit"] == statuses["90.fit"] == "imported"
    assert (
        "rate limit"
        in next(
            f
            for f in body["files"]
            if f["filename"] == f"{users_router.FIT_ANALYSIS_RECENT_DAYS}.fit"
        )["message"].lower()
    )
    assert (
        "rate limit"
        in next(f for f in body["files"] if f["filename"] == "2.fit")["message"].lower()
    )
    assert mock_ai_service["analyse_fit_activity"].await_count == 2
    assert await _snapshots() == 2


@pytest.mark.asyncio
async def test_a_file_that_does_not_parse_costs_nothing(
    client: AsyncClient, auth_headers, mock_ai_service, tight_limit
):
    response = await client.post(
        BULK,
        headers=auth_headers,
        files=[
            ("files", (f"bad{i}.fit", b"not a number", "application/octet-stream"))
            for i in range(3)
        ]
        + [
            ("files", ("0.fit", b"0", "application/octet-stream")),
            ("files", ("1.fit", b"1", "application/octet-stream")),
        ],
    )

    body = response.json()
    assert [f["status"] for f in body["files"]] == [
        "failed",
        "failed",
        "failed",
        "imported",
        "imported",
    ]


def test_a_ride_with_no_readable_date_is_treated_as_recent():
    class Parsed:
        ride_date = "someday"

    assert users_router._is_recent_ride(Parsed(), app_today()) is True


@pytest.mark.asyncio
async def test_a_spent_allowance_does_not_refuse_the_history_behind_it(
    client: AsyncClient, auth_headers, mock_ai_service, tight_limit
):
    """Recent rides first, then the old ones (review of app PR #978)."""
    body = await _upload(client, auth_headers, 0, 1, 2, 30, 60, 3, 90)

    statuses = {f["filename"]: f["status"] for f in body["files"]}
    # Burst of two: 0 and 1 are paid for, 2 and 3 find it spent.
    assert statuses["0.fit"] == statuses["1.fit"] == "imported"
    assert statuses["2.fit"] == statuses["3.fit"] == "failed"
    # The history costs nothing, so the limit does not stop it.
    assert statuses["30.fit"] == statuses["60.fit"] == statuses["90.fit"] == "imported"
    assert mock_ai_service["analyse_fit_activity"].await_count == 2
