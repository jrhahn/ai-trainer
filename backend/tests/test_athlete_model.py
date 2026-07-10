"""Tests for the long-term structured athlete model (#384)."""

from __future__ import annotations

import pytest

import crud
from services import ai_service
from services.prompts import athlete_model_section
from tests.conftest import TestSessionLocal


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


# ---------------------------------------------------------------------------
# GET / PUT routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_defaults_to_empty_model(client, auth_headers):
    response = await client.get("/api/v1/users/me/athlete-model", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ftpWatts"] is None
    assert body["strengths"] == []
    assert body["thresholdDurability"] == ""
    assert body["confidence"] == 0.0


@pytest.mark.asyncio
async def test_put_persists_and_roundtrips(client, auth_headers):
    payload = {
        "ftpWatts": 268,
        "vo2max": 59.5,
        "pacingQuality": "even pacing",
        "thresholdDurability": "holds 30 min",
        "strengths": ["threshold", "consistency"],
        "weaknesses": ["sprint"],
        "riskFactors": ["ramps quickly"],
        "preferredTrainingStyle": "structured",
        "summary": "Durable threshold rider.",
    }
    put = await client.put(
        "/api/v1/users/me/athlete-model", headers=auth_headers, json=payload
    )
    assert put.status_code == 200
    assert put.json()["ftpWatts"] == 268
    assert put.json()["strengths"] == ["threshold", "consistency"]

    got = await client.get("/api/v1/users/me/athlete-model", headers=auth_headers)
    assert got.json()["vo2max"] == 59.5
    assert got.json()["summary"] == "Durable threshold rider."


@pytest.mark.asyncio
async def test_put_does_not_touch_coach_owned_confidence(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    # Coach derives a model with a confidence score.
    async with TestSessionLocal() as db:
        await crud.upsert_athlete_model(
            db, user_id, summary="derived", confidence=0.8
        )
        await db.commit()

    # Athlete edits via the API (no confidence in the request body).
    await client.put(
        "/api/v1/users/me/athlete-model",
        headers=auth_headers,
        json={"summary": "edited by athlete"},
    )

    async with TestSessionLocal() as db:
        model = await crud.get_athlete_model(db, user_id)
    assert model.summary == "edited by athlete"
    assert model.confidence == 0.8  # preserved


# ---------------------------------------------------------------------------
# derive_athlete_model normalisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derive_normalises_llm_payload(monkeypatch):
    async def fake_chat(provider, system, user, *, json_mode, task):
        return (
            '{"ftpWatts": "271", "vo2max": null, "pacingQuality": "  even  ", '
            '"recoveryAbility": "", "thresholdDurability": "holds well", '
            '"heatTolerance": "", "preferredTrainingStyle": "intervals", '
            '"strengths": ["climbing", 5, "   "], "weaknesses": [], '
            '"riskFactors": ["overreaches"], "summary": "Strong climber.", '
            '"confidence": 1.5}'
        )

    monkeypatch.setattr(ai_service, "_chat", fake_chat)

    result = await ai_service.derive_athlete_model("some ride history")
    assert result is not None
    assert result["ftp_watts"] == 271  # coerced from string
    assert result["vo2max"] is None
    assert result["pacing_quality"] == "even"  # trimmed
    assert result["strengths"] == ["climbing"]  # non-strings / blanks dropped
    assert result["risk_factors"] == ["overreaches"]
    assert result["confidence"] == 0.9  # clamped to the 0.9 ceiling


@pytest.mark.asyncio
async def test_derive_returns_none_for_empty_history():
    assert await ai_service.derive_athlete_model("   ") is None


# ---------------------------------------------------------------------------
# prompt section
# ---------------------------------------------------------------------------


def test_athlete_model_section_omits_empty_and_metadata():
    section = athlete_model_section(
        {
            "ftpWatts": 260,
            "vo2max": None,
            "pacingQuality": "",
            "thresholdDurability": "holds 30 min",
            "strengths": ["threshold"],
            "weaknesses": [],
            "confidence": 0.7,
            "updatedAt": "2026-07-10T00:00:00Z",
        }
    )
    assert "260" in section
    assert "holds 30 min" in section
    assert "threshold" in section
    assert "0.7" not in section  # confidence is metadata, not surfaced
    assert "2026-07-10" not in section  # updatedAt suppressed


def test_athlete_model_section_blank_when_no_signal():
    assert athlete_model_section(None) == ""
    assert athlete_model_section({"pacingQuality": "", "strengths": []}) == ""
