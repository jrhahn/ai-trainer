"""The intervals analyse path must persist a precision-safe activity id (#429 Bug B).

The frontend renders the 19-digit intervals hash as a JS Number, which loses
precision. When the payload also carries the raw ``externalId`` string, the
backend must key persistence off that raw id (via ``intervals_activity_id``)
rather than the corrupted numeric ``id``.
"""

import pytest

import crud
from services.intervals_service import intervals_activity_id
from tests.conftest import TestSessionLocal


@pytest.mark.asyncio
async def test_intervals_analyse_uses_raw_external_id_not_corrupted_number(
    client, auth_headers, mock_ai_service
):
    raw_id = "i166933341"
    true_hash = intervals_activity_id(raw_id)
    # What the JS Number becomes after the float64 round-trip of ``true_hash``.
    corrupted_numeric = int(float(true_hash))
    assert corrupted_numeric != true_hash  # precondition: the corruption is real

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "source": "intervals",
            "activities": [
                {
                    "id": corrupted_numeric,
                    "externalId": raw_id,
                    "name": "Long ride with a coffee stop",
                    "type": "Ride",
                    "distance": 90000,
                    "movingTime": 15914,
                    "elapsedTime": 30491,
                    "totalElevationGain": 800,
                    "startDate": "2026-07-18T06:00:00Z",
                    "averageWatts": 185,
                }
            ],
            "currentFtp": 260,
        },
    )
    assert response.status_code == 200

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        metrics = await crud.get_all_ride_metrics_ordered(db, user.id)

    assert len(metrics) == 1
    metric = metrics[0]
    # Stored id is the true, uncorrupted hash — not the float64-mangled number.
    assert metric.strava_activity_id == true_hash
    assert metric.strava_activity_id != corrupted_numeric
    # And the duration is moving_time, not the elapsed value.
    assert metric.duration_seconds == 15914
