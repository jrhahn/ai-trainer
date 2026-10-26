"""The coach chat's timeline in one request (ai-trainer-ops#277).

The chat fetched five lists after every reply. ``GET /users/me/coach-timeline``
returns them together, built by the five routes themselves.
"""

from __future__ import annotations

import pytest

import crud
from tests.conftest import TestSessionLocal

LISTS = [
    ("planHistory", "plan-history", "entries"),
    ("openQuestions", "open-questions", "openQuestions"),
    ("hypotheses", "athlete-hypotheses", "hypotheses"),
    ("experiments", "validation-experiments", "experiments"),
    ("inquiries", "inquiries", "inquiries"),
]


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


@pytest.mark.asyncio
async def test_the_timeline_holds_the_five_lists_as_their_own_routes_return_them(
    client, auth_headers
):
    user_id = await _current_user_id(client, auth_headers)
    async with TestSessionLocal() as db:
        await crud.record_athlete_open_question(
            db, user_id, question="Is FTP underestimated?"
        )
        await crud.record_athlete_inquiry(
            db, user_id, question="What gets in the way on Tuesdays?"
        )
        await db.commit()

    timeline = await client.get("/api/v1/users/me/coach-timeline", headers=auth_headers)
    assert timeline.status_code == 200
    body = timeline.json()
    assert sorted(body) == sorted(key for key, _, _ in LISTS)
    for key, path, field in LISTS:
        single = await client.get(f"/api/v1/users/me/{path}", headers=auth_headers)
        assert body[key] == single.json()[field], key
    assert [q["question"] for q in body["openQuestions"]] == ["Is FTP underestimated?"]
    assert [i["question"] for i in body["inquiries"]] == [
        "What gets in the way on Tuesdays?"
    ]


@pytest.mark.asyncio
async def test_the_timeline_needs_a_session(client):
    response = await client.get("/api/v1/users/me/coach-timeline")
    assert response.status_code == 401
