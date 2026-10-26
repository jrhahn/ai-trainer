"""Two more copies folded into one each (ai-trainer-ops#250).

routers/ai.py and routers/strava.py each had their own ``_optional_int``, and
``crud.get_ride_metric_by_external_id`` was ``get_ride_metric_by_source`` with
the source filter left out.
"""

from __future__ import annotations

import pytest

import crud
import routers.ai as ai_router
import routers.strava as strava_router
from services import activity_imports
from services.activity_imports import reported_int
from services.intervals_service import intervals_activity_id


def test_a_provider_number_is_rounded_and_anything_else_is_none():
    assert reported_int(141.6) == 142
    assert reported_int(150) == 150
    # A bool is an int to Python, and never a heart rate.
    assert reported_int(True) is None
    assert reported_int("150") is None
    assert reported_int(None) is None


def test_both_routers_hold_the_one_copy():
    assert ai_router.reported_int is activity_imports.reported_int
    assert strava_router.reported_int is activity_imports.reported_int
    assert not hasattr(ai_router, "_optional_int")
    assert not hasattr(strava_router, "_optional_int")


@pytest.mark.asyncio
async def test_one_query_finds_a_ride_by_its_id_from_one_source_or_from_any(db, user):
    await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=intervals_activity_id("i250"),
        activity_source="intervals",
        external_activity_id="i250",
        activity_date="2026-10-20",
        sport_type="cycling",
        duration_seconds=3600,
    )

    from_intervals = await crud.get_ride_metric_by_source(
        db, user.id, "intervals", "i250"
    )
    from_any = await crud.get_ride_metric_by_source(db, user.id, None, "i250")
    by_id = await crud.get_ride_metric_by_external_id(db, user.id, "i250")
    from_strava = await crud.get_ride_metric_by_source(db, user.id, "strava", "i250")

    assert from_intervals is not None
    assert from_any.id == by_id.id == from_intervals.id
    assert from_strava is None
