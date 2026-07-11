"""Tests for the athlete open-questions list (#385).

Covers crud accrual/auto-close, the management endpoints, memory export, the
coach-prompt injection, and the LLM candidate normalisation.
"""

from __future__ import annotations

import pytest

import crud
from services import ai_service
from services.prompts import ask_trainer_system, open_questions_section
from tests.conftest import TestSessionLocal


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


async def _record(user_id: str, **kwargs):
    async with TestSessionLocal() as db:
        question = await crud.record_athlete_open_question(db, user_id, **kwargs)
        await db.commit()
        return question.id


# ---------------------------------------------------------------------------
# crud
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_creates_open_question(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    async with TestSessionLocal() as db:
        question = await crud.record_athlete_open_question(
            db,
            user_id,
            question="Is FTP underestimated?",
            category="general",
            evidence="Recent VO2 intervals.",
            needs="30-minute threshold test.",
        )
        await db.commit()
    assert question.status == "open"
    assert question.evidence_count == 1
    assert question.evidence == "Recent VO2 intervals."
    assert question.needs == "30-minute threshold test."


@pytest.mark.asyncio
async def test_recurring_evidence_auto_closes_at_threshold(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    async with TestSessionLocal() as db:
        for _ in range(crud.ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE):
            question = await crud.record_athlete_open_question(
                db,
                user_id,
                question="Is FTP underestimated?",
                evidence="Yet another over-FTP effort.",
            )
        await db.commit()
    assert question.evidence_count == crud.ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE
    assert question.status == "answered"
    assert question.resolution


@pytest.mark.asyncio
async def test_resolved_flag_closes_immediately(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    async with TestSessionLocal() as db:
        question = await crud.record_athlete_open_question(
            db,
            user_id,
            question="Does the MTB position improve sustainable power?",
            resolved=True,
            resolution="Yes — 12W higher NP on the MTB over matched climbs.",
        )
        await db.commit()
    assert question.status == "answered"
    assert question.resolution.startswith("Yes")


@pytest.mark.asyncio
async def test_new_evidence_reopens_dismissed_question(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    question_id = await _record(
        user_id, question="Does strength training suppress heart-rate response?"
    )
    async with TestSessionLocal() as db:
        await crud.update_athlete_open_question(
            db, user_id, question_id, status="dismissed"
        )
        await db.commit()
    async with TestSessionLocal() as db:
        question = await crud.record_athlete_open_question(
            db,
            user_id,
            question="Does strength training suppress heart-rate response?",
            evidence="HR 6bpm low again after Tuesday's gym session.",
        )
        await db.commit()
    assert question.status == "open"
    assert question.evidence_count == 2


@pytest.mark.asyncio
async def test_record_rejects_blank_question(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    async with TestSessionLocal() as db:
        with pytest.raises(ValueError):
            await crud.record_athlete_open_question(db, user_id, question="   ")


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_open_questions_only(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _record(user_id, question="Is FTP underestimated?")

    response = await client.get(
        "/api/v1/users/me/open-questions", headers=auth_headers
    )
    assert response.status_code == 200
    questions = response.json()["openQuestions"]
    assert len(questions) == 1
    assert questions[0]["question"] == "Is FTP underestimated?"
    assert questions[0]["status"] == "open"
    assert questions[0]["evidenceCount"] == 1


@pytest.mark.asyncio
async def test_dismiss_hides_from_open_list(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    question_id = await _record(user_id, question="Is FTP underestimated?")

    patched = await client.patch(
        f"/api/v1/users/me/open-questions/{question_id}",
        headers=auth_headers,
        json={"status": "dismissed"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "dismissed"

    open_only = await client.get(
        "/api/v1/users/me/open-questions", headers=auth_headers
    )
    assert open_only.json()["openQuestions"] == []

    with_resolved = await client.get(
        "/api/v1/users/me/open-questions?includeResolved=true",
        headers=auth_headers,
    )
    assert len(with_resolved.json()["openQuestions"]) == 1


@pytest.mark.asyncio
async def test_patch_empty_question_is_422(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    question_id = await _record(user_id, question="Is FTP underestimated?")

    response = await client.patch(
        f"/api/v1/users/me/open-questions/{question_id}",
        headers=auth_headers,
        json={"question": "   "},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_and_missing_are_204_then_404(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    question_id = await _record(user_id, question="Is FTP underestimated?")

    deleted = await client.delete(
        f"/api/v1/users/me/open-questions/{question_id}", headers=auth_headers
    )
    assert deleted.status_code == 204

    missing = await client.delete(
        f"/api/v1/users/me/open-questions/{question_id}", headers=auth_headers
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_question_is_404(client, auth_headers):
    response = await client.patch(
        "/api/v1/users/me/open-questions/does-not-exist",
        headers=auth_headers,
        json={"status": "answered"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_export_includes_open_questions(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _record(user_id, question="Is FTP underestimated?")

    response = await client.get(
        "/api/v1/users/me/memory-export", headers=auth_headers
    )
    assert response.status_code == 200
    questions = [q["question"] for q in response.json()["openQuestions"]]
    assert "Is FTP underestimated?" in questions


@pytest.mark.asyncio
async def test_clear_memory_removes_open_questions(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _record(user_id, question="Is FTP underestimated?")

    cleared = await client.delete("/api/v1/users/me/memory", headers=auth_headers)
    assert cleared.status_code == 204

    async with TestSessionLocal() as db:
        remaining = await crud.list_athlete_open_questions(
            db, user_id, include_resolved=True
        )
    assert remaining == []


# ---------------------------------------------------------------------------
# coach-prompt injection
# ---------------------------------------------------------------------------


def test_open_questions_section_renders_open_only():
    section = open_questions_section(
        [
            {
                "question": "Is FTP underestimated?",
                "evidence": "Recent VO2 intervals.",
                "needs": "30-minute threshold test.",
                "status": "open",
            },
            {"question": "Already answered?", "status": "answered"},
        ]
    )
    assert "Is FTP underestimated?" in section
    assert "30-minute threshold test." in section
    assert "Already answered?" not in section


def test_open_questions_section_empty_when_no_open():
    assert open_questions_section([]) == ""
    assert open_questions_section([{"question": "x", "status": "answered"}]) == ""


def test_ask_trainer_system_includes_open_questions():
    prompt = ask_trainer_system(
        {"name": "Rider"},
        "2026-07-11",
        [],
        [],
        "",
        "",
        "",
        "",
        open_questions=[
            {"question": "Is FTP underestimated?", "status": "open"}
        ],
    )
    assert "Is FTP underestimated?" in prompt


# ---------------------------------------------------------------------------
# LLM candidate normalisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_open_questions_normalises_and_dedupes(monkeypatch):
    async def fake_chat(*args, **kwargs):
        return (
            '{"candidates": ['
            '{"question": "Is FTP underestimated?", "evidence": "e", '
            '"needs": "n", "category": "general"},'
            '{"question": "Is FTP underestimated?", "evidence": "dup"},'
            '{"nonsense": true}]}'
        )

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    candidates = await ai_service.generate_open_questions("history block")
    assert len(candidates) == 1
    assert candidates[0]["question"] == "Is FTP underestimated?"
    assert candidates[0]["category"] == "general"
    assert candidates[0]["resolved"] is False


@pytest.mark.asyncio
async def test_generate_open_questions_empty_history_short_circuits(monkeypatch):
    async def fail_chat(*args, **kwargs):
        raise AssertionError("should not call the model for empty history")

    monkeypatch.setattr(ai_service, "_chat", fail_chat)
    assert await ai_service.generate_open_questions("   ") == []
