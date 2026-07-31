"""Tests for the questions the coach puts to the athlete directly (#506).

Covers crud lifecycle, the inferability-gated generation pass, the answer
judgement (accept / rephrase once / hand off to settings), the endpoints, and
the coach-prompt injection that stops the coach asking a pinned question twice.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import ai_service, athlete_inquiry
from services.prompts import (
    ask_trainer_system,
    generate_inquiries_system,
    evaluate_inquiry_answer_system,
    pending_inquiries_section,
)
from tests.conftest import TestSessionLocal


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


async def _record(user_id: str, **kwargs) -> models.AthleteInquiry:
    async with TestSessionLocal() as db:
        inquiry = await crud.record_athlete_inquiry(db, user_id, **kwargs)
        await db.commit()
        return inquiry


async def _get(user_id: str, inquiry_id: str) -> models.AthleteInquiry:
    async with TestSessionLocal() as db:
        return await crud.get_athlete_inquiry(db, user_id, inquiry_id)


# ---------------------------------------------------------------------------
# crud
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_creates_pending_inquiry(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(
        user_id,
        question="You skipped Tuesday three weeks running — what's getting in the way?",
        category="recurring_issues",
        why_asking="Your rides show the absence but never the reason.",
        settings_hint="Settings > Athlete > Availability",
    )
    assert inquiry.status == "pending"
    assert inquiry.ask_count == 1
    assert inquiry.answer is None
    assert inquiry.settings_hint == "Settings > Athlete > Availability"


@pytest.mark.asyncio
async def test_recording_the_same_question_twice_does_not_ask_again(
    client, auth_headers
):
    """Re-raising must not nag: unlike an open question, asking twice is a cost."""
    user_id = await _current_user_id(client, auth_headers)
    await _record(user_id, question="Is the knee still bothering you?")
    duplicate = await _record(user_id, question="is the KNEE still bothering you?  ")
    assert duplicate is None

    async with TestSessionLocal() as db:
        stored = await crud.list_athlete_inquiries(db, user_id, include_resolved=True)
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_dismissed_inquiry_is_reopened_but_answered_one_is_not(
    client, auth_headers
):
    """A skip means "not now"; an answer means never ask again."""
    user_id = await _current_user_id(client, auth_headers)
    skipped = await _record(user_id, question="What do you want from this season?")
    async with TestSessionLocal() as db:
        await crud.dismiss_athlete_inquiry(db, user_id, skipped.id)
        await db.commit()

    reopened = await _record(user_id, question="What do you want from this season?")
    assert reopened is not None
    assert reopened.status == "pending"
    assert reopened.ask_count == 1

    async with TestSessionLocal() as db:
        inquiry = await crud.get_athlete_inquiry(db, user_id, skipped.id)
        await crud.resolve_athlete_inquiry(db, inquiry, answer="A hilly gran fondo.")
        await db.commit()

    assert await _record(user_id, question="What do you want from this season?") is None


@pytest.mark.asyncio
async def test_pending_count_ignores_resolved_inquiries(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    first = await _record(user_id, question="Did the power meter change?")
    await _record(user_id, question="How is sleep at the moment?")
    async with TestSessionLocal() as db:
        inquiry = await crud.get_athlete_inquiry(db, user_id, first.id)
        await crud.resolve_athlete_inquiry(db, inquiry, answer="New pedals in May.")
        await db.commit()
        assert await crud.count_pending_athlete_inquiries(db, user_id) == 1


# ---------------------------------------------------------------------------
# answer lifecycle
# ---------------------------------------------------------------------------


async def _user(user_id: str) -> models.User:
    async with TestSessionLocal() as db:
        return await db.get(models.User, user_id)


@pytest.mark.asyncio
async def test_accepted_answer_closes_inquiry_and_stores_a_trusted_fact(
    client, auth_headers, monkeypatch
):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(
        user_id,
        question="Is the knee still bothering you?",
        category="recurring_issues",
    )

    async def fake_evaluate(question, answer, **kwargs):
        assert "knee" in question
        return {
            "answered": True,
            "fact": "Knee pain resolved after a physio block in May 2026.",
            "reply": "Good to hear — I'll put the hills back in.",
            "question": "",
        }

    monkeypatch.setattr(ai_service, "evaluate_inquiry_answer", fake_evaluate)

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        stored = await crud.get_athlete_inquiry(db, user_id, inquiry.id)
        result, accepted, reply = await athlete_inquiry.submit_inquiry_answer(
            db, user, stored, "All good now, physio sorted it."
        )
        await db.commit()

    assert accepted is True
    assert reply.startswith("Good to hear")
    assert result.status == "answered"
    assert result.answer == "All good now, physio sorted it."

    async with TestSessionLocal() as db:
        facts = await crud.list_athlete_memory_facts(db, user_id)
    stored_fact = next(
        (f for f in facts if "Knee pain resolved" in f.fact), None
    )
    assert stored_fact is not None, "an answered inquiry must become durable knowledge"
    assert stored_fact.kind == "fact"
    # First-hand testimony is trusted immediately rather than earning trust by
    # recurrence, so it clears the prompt-inclusion threshold on the first write.
    assert stored_fact.confidence >= 0.5


@pytest.mark.asyncio
async def test_answer_that_misses_is_rephrased_and_asked_once_more(
    client, auth_headers, monkeypatch
):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(user_id, question="What are you training for this year?")

    async def fake_evaluate(question, answer, *, is_final_attempt=False, **kwargs):
        assert is_final_attempt is False
        return {
            "answered": False,
            "fact": "",
            "reply": "Fair enough — let me put it another way.",
            "question": "Is there a particular event or date you're building towards?",
        }

    monkeypatch.setattr(ai_service, "evaluate_inquiry_answer", fake_evaluate)

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        stored = await crud.get_athlete_inquiry(db, user_id, inquiry.id)
        result, accepted, _ = await athlete_inquiry.submit_inquiry_answer(
            db, user, stored, "dunno really"
        )
        await db.commit()

    assert accepted is False
    assert result.status == "pending", "it stays pinned for the second attempt"
    assert result.ask_count == 2
    assert result.question == "Is there a particular event or date you're building towards?"
    assert result.answer == "dunno really", "a partial answer is still kept"


@pytest.mark.asyncio
async def test_second_miss_hands_off_to_settings_instead_of_asking_again(
    client, auth_headers, monkeypatch
):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(
        user_id,
        question="What are you training for this year?",
        settings_hint="Settings > Athlete > Goals",
    )
    async with TestSessionLocal() as db:
        stored = await crud.get_athlete_inquiry(db, user_id, inquiry.id)
        await crud.reask_athlete_inquiry(
            db, stored, answer="dunno", question="Any event in mind?", note="rephrased"
        )
        await db.commit()

    seen_final: list[bool] = []

    async def fake_evaluate(question, answer, *, is_final_attempt=False, **kwargs):
        seen_final.append(is_final_attempt)
        return {
            "answered": False,
            "fact": "",
            "reply": "No problem — you can set this any time under Settings > Athlete > Goals.",
            "question": "",
        }

    monkeypatch.setattr(ai_service, "evaluate_inquiry_answer", fake_evaluate)

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        stored = await crud.get_athlete_inquiry(db, user_id, inquiry.id)
        result, accepted, reply = await athlete_inquiry.submit_inquiry_answer(
            db, user, stored, "still dunno"
        )
        await db.commit()

    assert seen_final == [True]
    assert accepted is False
    assert result.status == "needs_settings"
    assert result.ask_count == crud.ATHLETE_INQUIRY_MAX_ASKS, "never asked a third time"
    assert "Settings" in (result.follow_up_note or "")


@pytest.mark.asyncio
async def test_a_broken_answer_judge_accepts_rather_than_re_asking(monkeypatch):
    """A failed judge must not cost the athlete a repeated question."""

    async def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ai_service, "_chat", boom)

    verdict = await ai_service.evaluate_inquiry_answer(
        "Is the knee still bothering you?", "yes, a bit"
    )
    assert verdict["answered"] is True


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------


async def _create_user_with_history(*, email: str, activity_count: int) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Test Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        for i in range(activity_count):
            db.add(
                models.RideMetric(
                    user_id=user.id,
                    strava_activity_id=95000 + i,
                    activity_source="strava",
                    external_activity_id=str(95000 + i),
                    activity_date=f"2026-05-{(i % 28) + 1:02d}",
                    activity_start_datetime=f"2026-05-{(i % 28) + 1:02d}T08:00:00",
                    activity_name=f"Ride {i}",
                    sport_type="Ride",
                    duration_seconds=3600,
                    tss=60.0,
                )
            )
        await db.commit()
        return user.id


_SAMPLE_CANDIDATES = [
    {
        "question": "You cut Sunday's long ride short twice — what happened?",
        "category": "recurring_issues",
        "why_asking": "The file shows the early finish but never the reason.",
        "settings_hint": "Settings > Athlete > Availability",
    },
    {
        "question": "What are you building towards this season?",
        "category": "goals_motivation",
        "why_asking": "No amount of riding tells me what you actually want.",
        "settings_hint": "Settings > Athlete > Goals",
    },
]


@pytest.mark.asyncio
async def test_generation_pins_questions_for_the_athlete(monkeypatch):
    user_id = await _create_user_with_history(email="inq@example.com", activity_count=10)

    async def fake_generate(
        metrics_section, *, existing_facts, asked_questions, max_candidates, provider
    ):
        assert metrics_section
        assert max_candidates == crud.ATHLETE_INQUIRY_MAX_PENDING
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        athlete_inquiry.ai_service, "generate_athlete_inquiries", fake_generate
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        recorded = await athlete_inquiry.generate_user_inquiries(
            db, user, now=datetime(2026, 6, 8, tzinfo=timezone.utc), timezone_name="UTC"
        )
        await db.commit()

    assert recorded == 2
    async with TestSessionLocal() as db:
        stored = await crud.list_athlete_inquiries(db, user_id)
    assert {i.question for i in stored} == {c["question"] for c in _SAMPLE_CANDIDATES}
    assert all(i.why_asking for i in stored), "the athlete is told why they're asked"


@pytest.mark.asyncio
async def test_generation_stops_once_the_pending_limit_is_reached(monkeypatch):
    """The athlete meets a question or two, never a questionnaire."""
    user_id = await _create_user_with_history(
        email="inqfull@example.com", activity_count=10
    )
    for index in range(crud.ATHLETE_INQUIRY_MAX_PENDING):
        await _record(user_id, question=f"Existing question {index}?")

    called = False

    async def fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        athlete_inquiry.ai_service, "generate_athlete_inquiries", fake_generate
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        recorded = await athlete_inquiry.generate_user_inquiries(db, user)

    assert recorded == 0
    assert called is False, "no LLM call when there is no room to ask"


@pytest.mark.asyncio
async def test_generation_skips_users_with_too_little_history(monkeypatch):
    user_id = await _create_user_with_history(
        email="inqthin@example.com",
        activity_count=athlete_inquiry.INQUIRY_MIN_ACTIVITIES - 1,
    )
    called = False

    async def fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        athlete_inquiry.ai_service, "generate_athlete_inquiries", fake_generate
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        assert await athlete_inquiry.generate_user_inquiries(db, user) == 0
    assert called is False


@pytest.mark.asyncio
async def test_generation_passes_previously_asked_questions_to_the_model(monkeypatch):
    """Answered and dismissed questions still count as asked."""
    user_id = await _create_user_with_history(
        email="inqasked@example.com", activity_count=10
    )
    answered = await _record(user_id, question="Did the power meter change?")
    async with TestSessionLocal() as db:
        stored = await crud.get_athlete_inquiry(db, user_id, answered.id)
        await crud.resolve_athlete_inquiry(db, stored, answer="Yes, in May.")
        await db.commit()

    seen: dict = {}

    async def fake_generate(metrics_section, *, asked_questions, **kwargs):
        seen["asked"] = asked_questions
        return []

    monkeypatch.setattr(
        athlete_inquiry.ai_service, "generate_athlete_inquiries", fake_generate
    )

    async with TestSessionLocal() as db:
        user = await db.get(models.User, user_id)
        await athlete_inquiry.generate_user_inquiries(db, user)

    assert "Did the power meter change?" in seen["asked"]


# ---------------------------------------------------------------------------
# LLM candidate normalisation
# ---------------------------------------------------------------------------


def test_inquiry_candidate_normalisation_accepts_camel_and_snake_case():
    candidate = ai_service._normalise_inquiry_candidate(
        {
            "question": "  Why did you skip Tuesday?  ",
            "whyAsking": "The data shows absence, not reason.",
            "settings_hint": "Settings > Athlete",
            "category": "recurring_issues",
        }
    )
    assert candidate == {
        "question": "Why did you skip Tuesday?",
        "category": "recurring_issues",
        "why_asking": "The data shows absence, not reason.",
        "settings_hint": "Settings > Athlete",
    }


def test_inquiry_candidate_without_a_question_is_rejected():
    assert ai_service._normalise_inquiry_candidate({"whyAsking": "no question"}) is None
    assert ai_service._normalise_inquiry_candidate("not a dict") is None


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


def test_generation_prompt_forces_the_inferability_check_first():
    """The whole point of the pass: don't ask what the next few rides will tell you."""
    prompt = generate_inquiries_system(2)
    assert "STEP 1" in prompt and "STEP 2" in prompt
    assert "NEXT FEW WEEKS OF DATA WILL TELL YOU" in prompt
    assert "must NOT ask about any of it" in prompt
    assert "NEVER reveal" in prompt or "never reveal" in prompt.lower()


def test_answer_evaluation_prompt_switches_from_rephrasing_to_hand_off():
    rephrase = evaluate_inquiry_answer_system(is_final_attempt=False)
    final = evaluate_inquiry_answer_system(is_final_attempt=True)
    assert "Ask ONCE more" in rephrase
    assert "LAST attempt" in final and "Do NOT ask again" in final
    assert "settings" in final.lower()


def test_pending_inquiries_section_lists_only_pending_questions():
    section = pending_inquiries_section(
        [
            {"question": "Is the knee okay?", "whyAsking": "cannot see pain", "status": "pending"},
            {"question": "Already answered", "status": "answered"},
        ]
    )
    assert "Is the knee okay?" in section
    assert "Already answered" not in section
    assert "do NOT ask them" in section


def test_pending_inquiries_section_is_empty_without_pending_questions():
    assert pending_inquiries_section(None) == ""
    assert pending_inquiries_section([{"question": "done", "status": "answered"}]) == ""


def test_coach_prompt_carries_the_pinned_questions():
    prompt = ask_trainer_system(
        {"name": "Test"},
        "2026-06-08",
        [],
        [],
        "",
        "",
        "",
        "",
        pending_inquiries=[
            {"question": "Is the knee still bothering you?", "status": "pending"}
        ],
    )
    assert "Is the knee still bothering you?" in prompt


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_endpoint_returns_only_pending_inquiries(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    pending = await _record(user_id, question="How is sleep?")
    answered = await _record(user_id, question="Did the bike change?")
    async with TestSessionLocal() as db:
        stored = await crud.get_athlete_inquiry(db, user_id, answered.id)
        await crud.resolve_athlete_inquiry(db, stored, answer="No.")
        await db.commit()

    response = await client.get("/api/v1/users/me/inquiries", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()["inquiries"]
    assert [i["id"] for i in body] == [pending.id]
    assert body[0]["status"] == "pending"
    assert body[0]["askCount"] == 1

    response = await client.get(
        "/api/v1/users/me/inquiries?includeResolved=true", headers=auth_headers
    )
    assert len(response.json()["inquiries"]) == 2


@pytest.mark.asyncio
async def test_answer_endpoint_records_the_exchange_in_the_chat(
    client, auth_headers, monkeypatch
):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(user_id, question="Is the knee still bothering you?")

    async def fake_evaluate(*args, **kwargs):
        return {
            "answered": True,
            "fact": "Knee is pain-free as of June 2026.",
            "reply": "Great — hills go back in next week.",
            "question": "",
        }

    monkeypatch.setattr(ai_service, "evaluate_inquiry_answer", fake_evaluate)

    response = await client.post(
        f"/api/v1/users/me/inquiries/{inquiry.id}/answer",
        headers=auth_headers,
        json={"answer": "All fine now."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["inquiry"]["status"] == "answered"
    assert body["coachReply"] == "Great — hills go back in next week."

    async with TestSessionLocal() as db:
        messages = await crud.get_chat_messages(db, user_id)
    contents = [m.content for m in messages]
    assert "Is the knee still bothering you?" in contents
    assert "All fine now." in contents
    assert "Great — hills go back in next week." in contents


@pytest.mark.asyncio
async def test_answering_a_resolved_inquiry_conflicts(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(user_id, question="How is sleep?")
    async with TestSessionLocal() as db:
        stored = await crud.get_athlete_inquiry(db, user_id, inquiry.id)
        await crud.resolve_athlete_inquiry(db, stored, answer="Fine.")
        await db.commit()

    response = await client.post(
        f"/api/v1/users/me/inquiries/{inquiry.id}/answer",
        headers=auth_headers,
        json={"answer": "Still fine."},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_empty_answer_is_rejected(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(user_id, question="How is sleep?")
    response = await client.post(
        f"/api/v1/users/me/inquiries/{inquiry.id}/answer",
        headers=auth_headers,
        json={"answer": "   "},
    )
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_dismiss_and_delete_endpoints(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(user_id, question="What do you want this season?")

    response = await client.post(
        f"/api/v1/users/me/inquiries/{inquiry.id}/dismiss", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"

    response = await client.delete(
        f"/api/v1/users/me/inquiries/{inquiry.id}", headers=auth_headers
    )
    assert response.status_code == 204
    assert await _get(user_id, inquiry.id) is None


@pytest.mark.asyncio
async def test_inquiries_of_other_users_are_not_reachable(client, auth_headers):
    other_id = await _create_user_with_history(
        email="inqother@example.com", activity_count=1
    )
    inquiry = await _record(other_id, question="Someone else's question?")

    response = await client.post(
        f"/api/v1/users/me/inquiries/{inquiry.id}/answer",
        headers=auth_headers,
        json={"answer": "not mine"},
    )
    assert response.status_code == 404
    response = await client.delete(
        f"/api/v1/users/me/inquiries/{inquiry.id}", headers=auth_headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_memory_export_includes_inquiries_and_a_wipe_removes_them(
    client, auth_headers
):
    """Answers are the athlete's own words, so export and erasure must cover them."""
    user_id = await _current_user_id(client, auth_headers)
    inquiry = await _record(user_id, question="How is sleep at the moment?")
    async with TestSessionLocal() as db:
        stored = await crud.get_athlete_inquiry(db, user_id, inquiry.id)
        await crud.resolve_athlete_inquiry(db, stored, answer="Poor since the new job.")
        await db.commit()

    response = await client.get("/api/v1/users/me/memory-export", headers=auth_headers)
    assert response.status_code == 200
    exported = response.json()["inquiries"]
    assert [i["answer"] for i in exported] == ["Poor since the new job."]

    async with TestSessionLocal() as db:
        await crud.clear_athlete_memory(db, user_id)
        await db.commit()
        assert await crud.list_athlete_inquiries(db, user_id, include_resolved=True) == []
