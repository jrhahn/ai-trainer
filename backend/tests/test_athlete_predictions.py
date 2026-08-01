"""Router tests for prediction management endpoints (#383)."""

from __future__ import annotations

import pytest

import crud
from tests.conftest import TestSessionLocal


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


async def _seed_prediction(user_id: str, **kwargs) -> str:
    kwargs.setdefault("prediction", "The athlete will be fully recovered tomorrow.")
    kwargs.setdefault("expected_outcome", "Resting HR back to baseline.")
    async with TestSessionLocal() as db:
        prediction = await crud.record_athlete_prediction(db, user_id, **kwargs)
        await db.commit()
        return prediction.id


@pytest.mark.asyncio
async def test_list_returns_open_predictions_with_accuracy(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _seed_prediction(
        user_id,
        prediction="Fatigue will force an easier week within 10 days.",
        expected_outcome="A drop in weekly TSS.",
        horizon="within 10 days",
        confidence=0.6,
    )

    response = await client.get(
        "/api/v1/users/me/predictions", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    predictions = body["predictions"]
    assert len(predictions) == 1
    assert predictions[0]["expectedOutcome"] == "A drop in weekly TSS."
    assert predictions[0]["status"] == "pending"
    assert body["accuracy"] == {"evaluated": 0, "correct": 0, "accuracy": None}


@pytest.mark.asyncio
async def test_mark_correct_reduces_from_open_and_raises_confidence(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    prediction_id = await _seed_prediction(user_id, confidence=0.5)

    response = await client.patch(
        f"/api/v1/users/me/predictions/{prediction_id}",
        headers=auth_headers,
        json={"status": "correct", "actualOutcome": "Recovered as expected."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "correct"
    assert body["actualOutcome"] == "Recovered as expected."
    assert body["confidence"] == pytest.approx(0.7)

    open_list = await client.get(
        "/api/v1/users/me/predictions", headers=auth_headers
    )
    assert open_list.json()["predictions"] == []
    assert open_list.json()["accuracy"] == {
        "evaluated": 1,
        "correct": 1,
        "accuracy": 1.0,
    }


@pytest.mark.asyncio
async def test_mark_incorrect_reduces_confidence(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    prediction_id = await _seed_prediction(user_id, confidence=0.5)

    response = await client.patch(
        f"/api/v1/users/me/predictions/{prediction_id}",
        headers=auth_headers,
        json={"status": "incorrect", "actualOutcome": "Still fatigued."},
    )
    assert response.status_code == 200
    assert response.json()["confidence"] == pytest.approx(0.3)

    with_resolved = await client.get(
        "/api/v1/users/me/predictions?includeResolved=true",
        headers=auth_headers,
    )
    body = with_resolved.json()
    assert len(body["predictions"]) == 1
    assert body["accuracy"] == {"evaluated": 1, "correct": 0, "accuracy": 0.0}


@pytest.mark.asyncio
async def test_delete_prediction(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    prediction_id = await _seed_prediction(user_id)

    response = await client.delete(
        f"/api/v1/users/me/predictions/{prediction_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204

    missing = await client.delete(
        f"/api/v1/users/me/predictions/{prediction_id}",
        headers=auth_headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_prediction_is_404(client, auth_headers):
    response = await client.patch(
        "/api/v1/users/me/predictions/does-not-exist",
        headers=auth_headers,
        json={"status": "correct"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_empty_prediction_edit_is_422(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    prediction_id = await _seed_prediction(user_id)

    response = await client.patch(
        f"/api/v1/users/me/predictions/{prediction_id}",
        headers=auth_headers,
        json={"prediction": "   "},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_export_includes_predictions(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _seed_prediction(
        user_id,
        prediction="Power will improve after the recovery block.",
        expected_outcome="Higher 20-minute power on the next test.",
    )

    response = await client.get(
        "/api/v1/users/me/memory-export", headers=auth_headers
    )
    assert response.status_code == 200
    texts = [p["prediction"] for p in response.json()["predictions"]]
    assert "Power will improve after the recovery block." in texts
