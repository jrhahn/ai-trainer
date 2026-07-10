"""Tests for the long-term structured athlete model (#384)."""

from __future__ import annotations

import pytest

import crud
from services import ai_service
from services.ai_service import AIRateLimitError
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
# POST /ai/refresh-athlete-model
# ---------------------------------------------------------------------------


_DERIVED = {
    "ftp_watts": 272,
    "vo2max": 60.0,
    "pacing_quality": "even",
    "recovery_ability": "",
    "threshold_durability": "holds 30 min",
    "heat_tolerance": "",
    "preferred_training_style": "intervals",
    "strengths": ["threshold"],
    "weaknesses": [],
    "risk_factors": [],
    "summary": "Durable rider.",
    "confidence": 0.65,
}


@pytest.mark.asyncio
async def test_refresh_persists_derived_model(client, auth_headers, monkeypatch):
    async def fake_derive(metrics_section, *, current_model=None, provider="openai"):
        return dict(_DERIVED)

    monkeypatch.setattr(ai_service, "derive_athlete_model", fake_derive)

    response = await client.post(
        "/api/v1/ai/refresh-athlete-model", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ftpWatts"] == 272
    assert body["thresholdDurability"] == "holds 30 min"
    assert body["confidence"] == pytest.approx(0.65)

    # Persisted, so a follow-up GET returns the same model.
    got = await client.get("/api/v1/users/me/athlete-model", headers=auth_headers)
    assert got.json()["ftpWatts"] == 272


@pytest.mark.asyncio
async def test_refresh_returns_existing_when_derive_yields_nothing(
    client, auth_headers, monkeypatch
):
    user_id = await _current_user_id(client, auth_headers)
    async with TestSessionLocal() as db:
        await crud.upsert_athlete_model(
            db, user_id, summary="prior", confidence=0.4
        )
        await db.commit()

    async def fake_derive(metrics_section, *, current_model=None, provider="openai"):
        # The existing model should be handed to the deriver.
        assert current_model is not None and current_model["summary"] == "prior"
        return None

    monkeypatch.setattr(ai_service, "derive_athlete_model", fake_derive)

    response = await client.post(
        "/api/v1/ai/refresh-athlete-model", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["summary"] == "prior"


@pytest.mark.asyncio
async def test_refresh_returns_empty_when_no_model_and_no_derivation(
    client, auth_headers, monkeypatch
):
    async def fake_derive(metrics_section, *, current_model=None, provider="openai"):
        return None

    monkeypatch.setattr(ai_service, "derive_athlete_model", fake_derive)

    response = await client.post(
        "/api/v1/ai/refresh-athlete-model", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["ftpWatts"] is None
    assert response.json()["summary"] == ""


@pytest.mark.asyncio
async def test_refresh_503_on_rate_limit(client, auth_headers, monkeypatch):
    async def boom(metrics_section, *, current_model=None, provider="openai"):
        raise AIRateLimitError("rate limited")

    monkeypatch.setattr(ai_service, "derive_athlete_model", boom)

    response = await client.post(
        "/api/v1/ai/refresh-athlete-model", headers=auth_headers
    )
    assert response.status_code == 503


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


@pytest.mark.asyncio
async def test_derive_returns_none_when_payload_is_not_an_object(monkeypatch):
    async def fake_chat(provider, system, user, *, json_mode, task):
        return "[]"  # a JSON array, not the expected object

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    assert await ai_service.derive_athlete_model("history") is None


@pytest.mark.asyncio
async def test_derive_tolerates_uncoercible_numbers(monkeypatch):
    async def fake_chat(provider, system, user, *, json_mode, task):
        # ftpWatts/vo2max/confidence are the wrong types; strengths isn't a list.
        return (
            '{"ftpWatts": {"bad": 1}, "vo2max": "abc", "strengths": "nope", '
            '"confidence": "high"}'
        )

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    result = await ai_service.derive_athlete_model("history")
    assert result is not None
    assert result["ftp_watts"] is None
    assert result["vo2max"] is None
    assert result["strengths"] == []
    assert result["confidence"] == 0.0


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
