"""The fields beside a coach reply: one sentence per changed day, and what the
reply claims happened (ai-trainer-ops#266, phase one).

The owner's answer on #136 was (b): add the fields and keep the rewrites, count
both checks for some weeks, then remove each regex whose cases the fields are
shown to cover. The owner's condition: the coach's prose stays free. So the
fields sit beside the reply and never land in its text, the prompt adds no fixed
sentence, and what is structured lives in the fields and the interface.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest

import crud
import services.ai_service as ai_service
from auth import decode_token
from services import metrics
from services.dates import app_today
from tests.conftest import TestSessionLocal


def _iso(days: int) -> str:
    return (app_today() + timedelta(days=days)).isoformat()


def _day(date: str, title: str = "Endurance Ride") -> dict:
    return {
        "date": date,
        "workoutType": "endurance",
        "title": title,
        "description": "Steady Z2",
        "durationMinutes": 90,
    }


def _count(check: str, prose: str, fields: str) -> float:
    return metrics.COACH_PROSE_CHECKS.labels(
        check=check, prose=prose, fields=fields
    )._value.get()


# --- reading the fields off the reply ----------------------------------------


def test_the_summaries_leave_the_updates_and_are_kept_beside_them():
    updates, summaries = ai_service._split_day_summaries(
        [
            {**_day(_iso(1)), "summary": "  Locker statt Intervalle.  "},
            {**_day(_iso(2)), "summary": ""},
            {**_day(_iso(3)), "summary": 42},
            _day(_iso(4)),
        ]
    )

    assert all("summary" not in update for update in updates)
    assert [update["date"] for update in updates] == [
        _iso(1),
        _iso(2),
        _iso(3),
        _iso(4),
    ]
    assert summaries == [{"date": _iso(1), "summary": "Locker statt Intervalle."}]


def test_a_summary_is_one_short_sentence_at_most():
    _, summaries = ai_service._split_day_summaries(
        [{**_day(_iso(1)), "summary": "x" * 1000}]
    )

    assert len(summaries[0]["summary"]) == ai_service._SUMMARY_MAX_CHARS


def test_no_updates_means_no_summaries():
    assert ai_service._split_day_summaries(None) == (None, [])


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"planChanged": True, "ftpChanged": False}, (True, False)),
        ({"planChanged": "yes"}, (None, None)),
        (None, (None, None)),
        ("true", (None, None)),
    ],
)
def test_the_claims_are_true_false_or_left_out(claims, expected):
    read = ai_service._claims_of({"claims": claims})

    assert (read["plan_changed"], read["ftp_changed"]) == expected


# --- the coach call ------------------------------------------------------------


async def _ask(reply: dict, plan: list[dict]) -> dict:
    async def fake_chat_history(provider, system_prompt, messages, **kwargs):
        return json.dumps(reply)

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        return await ai_service.ask_trainer(
            question="Kann ich morgen locker fahren?",
            plan=plan,
            profile={"name": "Test Rider", "currentFTP": 250},
        )


@pytest.mark.asyncio
async def test_the_reply_carries_its_summaries_and_claims_but_the_plan_does_not():
    plan = [_day(_iso(1), "Threshold Intervals"), _day(_iso(2))]
    result = await _ask(
        {
            "response": "Morgen fährst du locker: 60 Minuten statt der Intervalle.",
            "planUpdates": [
                {
                    **_day(_iso(1), "Lockere Runde"),
                    "durationMinutes": 60,
                    "summary": "Morgen locker 60 Minuten statt der Intervalle.",
                }
            ],
            "claims": {"planChanged": True, "ftpChanged": False},
        },
        plan,
    )

    assert result["day_summaries"] == [
        {"date": _iso(1), "summary": "Morgen locker 60 Minuten statt der Intervalle."}
    ]
    assert result["claims"] == {"plan_changed": True, "ftp_changed": False}
    assert "summary" not in result["plan_updates"][0]
    # The coach's own words are left as they were written.
    assert (
        result["response"]
        == "Morgen fährst du locker: 60 Minuten statt der Intervalle."
    )


@pytest.mark.asyncio
async def test_a_first_draft_is_counted_by_both_checks():
    """The reply claims a change, carries none, and says so in prose: both find it."""
    plan = [_day(_iso(1))]
    before = (
        _count("plan_claim", "found", "found"),
        _count("ftp_claim", "clean", "clean"),
    )

    async def replies(provider, system_prompt, messages, **kwargs):
        return json.dumps(
            {
                "response": "Ich habe deinen Plan angepasst: morgen ist jetzt locker.",
                "claims": {"planChanged": True, "ftpChanged": False},
            }
        )

    with patch.object(ai_service, "_chat_history", side_effect=replies) as chat:
        await ai_service.ask_trainer(
            question="Kann ich morgen locker fahren?",
            plan=plan,
            profile={"name": "Test Rider", "currentFTP": 250},
        )

    # The prose check asked for a rewrite; only the first draft is counted.
    assert chat.call_count == 2
    assert _count("plan_claim", "found", "found") == before[0] + 1
    assert _count("ftp_claim", "clean", "clean") == before[1] + 1


@pytest.mark.asyncio
async def test_a_change_the_reply_claims_and_carries_is_no_finding():
    plan = [_day(_iso(1), "Threshold Intervals")]
    clean_before = _count("plan_claim", "clean", "clean")
    found_before = _count("plan_claim", "clean", "found")

    await _ask(
        {
            "response": "Morgen fährst du locker statt der Intervalle.",
            "planUpdates": [
                {
                    **_day(_iso(1), "Lockere Runde"),
                    "durationMinutes": 60,
                    "summary": "Morgen locker 60 Minuten.",
                }
            ],
            "claims": {"planChanged": True, "ftpChanged": False},
        },
        plan,
    )

    assert _count("plan_claim", "clean", "clean") == clean_before + 1
    assert _count("plan_claim", "clean", "found") == found_before


@pytest.mark.asyncio
async def test_every_changed_day_with_its_sentence_is_named_by_the_fields():
    plan = [_day(_iso(1), "Threshold Intervals")]
    clean_before = _count("day_named", "clean", "clean")

    await _ask(
        {
            "response": "Morgen fährst du locker statt der Intervalle.",
            "planUpdates": [
                {
                    **_day(_iso(1), "Lockere Runde"),
                    "durationMinutes": 60,
                    "summary": "Morgen locker 60 Minuten.",
                }
            ],
            "claims": {"planChanged": True, "ftpChanged": False},
        },
        plan,
    )

    assert _count("day_named", "clean", "clean") == clean_before + 1


@pytest.mark.asyncio
async def test_a_claim_left_out_is_counted_as_absent():
    before = _count("plan_claim", "clean", "absent")

    await _ask({"response": "Gute Frage, morgen bleibt wie geplant."}, [_day(_iso(1))])

    assert _count("plan_claim", "clean", "absent") == before + 1


@pytest.mark.asyncio
async def test_a_changed_day_without_its_sentence_is_counted_by_the_fields():
    plan = [_day(_iso(1), "Threshold Intervals"), _day(_iso(2))]
    found_before = _count("day_named", "clean", "found")
    clean_before = _count("day_named", "clean", "clean")

    await _ask(
        {
            "response": "Morgen und übermorgen fährst du locker.",
            "planUpdates": [
                {**_day(_iso(1), "Lockere Runde"), "durationMinutes": 45},
                {
                    **_day(_iso(2), "Lockere Runde"),
                    "durationMinutes": 45,
                    "summary": "Übermorgen locker 45 Minuten.",
                },
            ],
            "claims": {"planChanged": True, "ftpChanged": False},
        },
        plan,
    )

    assert _count("day_named", "clean", "found") == found_before + 1
    assert _count("day_named", "clean", "clean") == clean_before


# --- what the athlete gets ------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_days_the_plan_took_get_their_sentence_and_never_in_the_text(
    client, auth_headers, mock_ai_service
):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [_day(_iso(2)), _day(_iso(3))])
        await db.commit()
    reply = "Übermorgen fährst du locker, 45 Minuten."
    mock_ai_service["ask_trainer"].return_value = {
        "response": reply,
        "plan_updates": [
            {**_day(_iso(2), "Lockere Runde"), "durationMinutes": 45},
            # A day the plan does not hold: dropped, so it gets no sentence.
            {**_day(_iso(9), "Lange Runde"), "durationMinutes": 180},
        ],
        "day_summaries": [
            {"date": _iso(2), "summary": "Übermorgen locker 45 Minuten."},
            {"date": _iso(9), "summary": "Nächste Woche eine lange Runde."},
        ],
        "claims": {"plan_changed": True, "ftp_changed": False},
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Kann ich übermorgen locker fahren?"},
    )

    assert response.status_code == 200, response.text
    shown = [{"date": _iso(2), "summary": "Übermorgen locker 45 Minuten."}]
    assert response.json()["daySummaries"] == shown
    history = (await client.get("/api/v1/users/me/chat", headers=auth_headers)).json()
    assistant = [m for m in history["messages"] if m["role"] == "assistant"][-1]
    assert assistant["daySummaries"] == shown
    # The coach's text is its own: no sentence of the fields is in it.
    assert assistant["content"] == reply
    async with TestSessionLocal() as db:
        stored = {
            d["date"]: d for d in (await crud.get_training_plan(db, user_id)).plan
        }
    assert "summary" not in stored[_iso(2)]


@pytest.mark.asyncio
async def test_a_reply_that_changes_nothing_shows_no_sentence(
    client, auth_headers, mock_ai_service
):
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Morgen bleibt wie geplant.",
        "day_summaries": [{"date": _iso(1), "summary": "Morgen locker."}],
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Bleibt morgen so?"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["daySummaries"] == []
