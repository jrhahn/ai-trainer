"""PATCH /users/me/ride-purpose/{id} — the answer the athlete could not give (#580).

The coach asked what an unclassified session was, in prose, in a card with no
answer field. These tests cover the affordance end to end: the answer lands on
the ride, it survives the next sync, and skipping is respected.
"""

from __future__ import annotations

import pytest

import crud
from database import async_session_maker
from services.ride_purpose_question import ATHLETE_STATED_CONFIDENCE


async def _import_unclassifiable_ride(client, auth_headers, activity_id: int) -> None:
    """Import an activity with no power stream, which classifies as unknown/low."""
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": activity_id,
                    "name": "Evening Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-22T18:00:00Z",
                }
            ]
        },
    )


async def _ride(client, auth_headers, activity_id: int) -> dict:
    history = await client.get(
        "/api/v1/users/me/ride-metrics-history", headers=auth_headers
    )
    rides = history.json()["rides"]
    return next(r for r in rides if r["stravaActivityId"] == activity_id)


@pytest.mark.asyncio
async def test_the_question_starts_open_on_an_unclassified_ride(
    client, auth_headers, mock_ai_service
):
    await _import_unclassifiable_ride(client, auth_headers, 6001)
    ride = await _ride(client, auth_headers, 6001)
    assert ride["ridePurpose"] == "unknown"
    assert ride["purposeQuestionStatus"] is None
    # The browser reads one boolean rather than re-deriving the rule; a copy of
    # it in TypeScript would drift from the one the prompts use.
    assert ride["purposeQuestionOpen"] is True


@pytest.mark.asyncio
async def test_the_answer_becomes_the_classification(
    client, auth_headers, mock_ai_service
):
    """Not a note beside the label — the label. Every prompt and badge that
    already reads ``ride_purpose`` then sees the athlete's answer for free."""
    await _import_unclassifiable_ride(client, auth_headers, 6002)

    response = await client.patch(
        "/api/v1/users/me/ride-purpose/6002",
        headers=auth_headers,
        json={"purpose": "interval_threshold"},
    )
    assert response.status_code == 200
    ride = response.json()["ride"]
    assert ride["ridePurpose"] == "interval_threshold"
    assert ride["classificationConfidence"] == ATHLETE_STATED_CONFIDENCE
    assert "athlete" in ride["classificationReason"].lower()
    assert ride["purposeQuestionStatus"] == "answered"
    # The stored one-line summary restates the classification; leaving it would
    # put "Unknown ride" next to the athlete's own answer.
    assert "Unknown" not in ride["summary"]
    assert "Threshold" in ride["summary"]
    assert ride["purposeQuestionOpen"] is False


@pytest.mark.asyncio
async def test_skipping_stops_the_question_without_asserting_anything(
    client, auth_headers, mock_ai_service
):
    await _import_unclassifiable_ride(client, auth_headers, 6003)

    response = await client.patch(
        "/api/v1/users/me/ride-purpose/6003",
        headers=auth_headers,
        json={"purpose": None},
    )
    assert response.status_code == 200
    ride = response.json()["ride"]
    assert ride["purposeQuestionStatus"] == "skipped"
    # Nothing was claimed about the session — that is the point of a skip.
    assert ride["ridePurpose"] == "unknown"
    assert ride["classificationConfidence"] == "low"
    # Still unclassified, but no longer asked about.
    assert ride["purposeQuestionOpen"] is False


@pytest.mark.asyncio
async def test_an_answer_outside_the_vocabulary_is_rejected(
    client, auth_headers, mock_ai_service
):
    """An answer nothing downstream can read would leave the question as
    consequence-free as the prose it replaces."""
    await _import_unclassifiable_ride(client, auth_headers, 6004)

    response = await client.patch(
        "/api/v1/users/me/ride-purpose/6004",
        headers=auth_headers,
        json={"purpose": "epic gravel adventure"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_answering_an_unknown_ride_is_a_404(client, auth_headers):
    response = await client.patch(
        "/api/v1/users/me/ride-purpose/999999",
        headers=auth_headers,
        json={"purpose": "endurance"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_endpoint_requires_auth(client):
    response = await client.patch(
        "/api/v1/users/me/ride-purpose/6005", json={"purpose": "endurance"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_re_import_does_not_overwrite_the_answer(
    client, auth_headers, mock_ai_service
):
    """Import re-runs on every sync and rebuilds the classification from scratch.

    Without the gate the athlete answers, the next sync silently puts the ride
    back to unknown/low, and the question they already answered reappears.
    """
    await _import_unclassifiable_ride(client, auth_headers, 6006)
    await client.patch(
        "/api/v1/users/me/ride-purpose/6006",
        headers=auth_headers,
        json={"purpose": "interval_vo2max"},
    )

    await _import_unclassifiable_ride(client, auth_headers, 6006)

    ride = await _ride(client, auth_headers, 6006)
    assert ride["ridePurpose"] == "interval_vo2max"
    assert ride["classificationConfidence"] == ATHLETE_STATED_CONFIDENCE
    assert ride["purposeQuestionStatus"] == "answered"


@pytest.mark.asyncio
async def test_a_re_import_still_updates_everything_else(
    client, auth_headers, mock_ai_service
):
    """The gate protects the athlete's answer, not the whole row — power, load
    and the plan match must keep arriving."""
    await _import_unclassifiable_ride(client, auth_headers, 6007)
    await client.patch(
        "/api/v1/users/me/ride-purpose/6007",
        headers=auth_headers,
        json={"purpose": "endurance"},
    )

    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 6007,
                    "name": "Evening Ride (corrected)",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-22T18:00:00Z",
                    "averageWatts": 205,
                }
            ]
        },
    )

    ride = await _ride(client, auth_headers, 6007)
    assert ride["ridePurpose"] == "endurance"
    assert ride["avgPowerW"] == 205


@pytest.mark.asyncio
async def test_a_reclassification_leaves_the_answer_alone(
    client, auth_headers, mock_ai_service
):
    """`update_ride_metric_classification` is the other write gate — the
    provider-interval backfill reaches the row through it."""
    await _import_unclassifiable_ride(client, auth_headers, 6008)
    await client.patch(
        "/api/v1/users/me/ride-purpose/6008",
        headers=auth_headers,
        json={"purpose": "interval_sprints"},
    )

    async with async_session_maker() as db:
        user = await crud.get_user_by_email(db, "rider@example.com")
        row = await crud.get_ride_metric_by_strava_id(db, user.id, 6008)
        await crud.update_ride_metric_classification(
            row,
            ride_purpose="tempo",
            classification_confidence="medium",
            classification_reason="Average power in tempo band.",
            summary="Tempo · 1h00m",
        )
        assert row.ride_purpose == "interval_sprints"
        assert row.classification_confidence == ATHLETE_STATED_CONFIDENCE
