"""'This is wrong' on coach replies and planned days (ai-trainer-ops#94)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import crud
import models
from config import settings
from routers import users as users_router
from scripts import wrong_flags_report
from tests.conftest import TestSessionLocal

FLAG = {
    "target": "reply",
    "ref": "2026-10-14T10:00:00.000Z",
    "excerpt": "Ride 4 h tomorrow.",
}


@pytest.fixture
def admin_enabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "super-secret")


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/v1/admin/login", json={"password": "super-secret"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_a_flag_is_stored_with_the_deployed_commit(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "git_sha", "abc1234")

    resp = await client.post(
        "/api/v1/users/me/wrong-flags",
        headers=auth_headers,
        json={**FLAG, "note": "  The race is the day after.  "},
    )

    assert resp.status_code == 201
    async with TestSessionLocal() as session:
        flag = (await session.scalars(select(models.WrongFlag))).one()
    assert (flag.target, flag.ref, flag.excerpt) == (
        "reply",
        FLAG["ref"],
        FLAG["excerpt"],
    )
    assert flag.note == "The race is the day after."
    assert flag.git_sha == "abc1234"


@pytest.mark.asyncio
async def test_a_blank_note_is_no_note(client, auth_headers):
    resp = await client.post(
        "/api/v1/users/me/wrong-flags",
        headers=auth_headers,
        json={**FLAG, "target": "day", "ref": "2026-10-15#0", "note": "   "},
    )

    assert resp.status_code == 201
    async with TestSessionLocal() as session:
        assert (await session.scalars(select(models.WrongFlag))).one().note is None


@pytest.mark.asyncio
async def test_whitespace_around_a_full_note_does_not_count(client, auth_headers):
    resp = await client.post(
        "/api/v1/users/me/wrong-flags",
        headers=auth_headers,
        json={**FLAG, "note": "x" * 1000 + "\n "},
    )

    assert resp.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"target": "chat"},
        {"excerpt": ""},
        {"excerpt": "x" * 4001},
        {"note": "x" * 1001},
        {"ref": ""},
    ],
)
async def test_a_malformed_flag_is_refused(client, auth_headers, change):
    resp = await client.post(
        "/api/v1/users/me/wrong-flags", headers=auth_headers, json={**FLAG, **change}
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_flags_need_a_session(client):
    resp = await client.post("/api/v1/users/me/wrong-flags", json=FLAG)

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_day_of_flags_has_a_ceiling(client, auth_headers, monkeypatch):
    monkeypatch.setattr(users_router, "WRONG_FLAGS_PER_DAY", 2)
    for _ in range(2):
        ok = await client.post(
            "/api/v1/users/me/wrong-flags", headers=auth_headers, json=FLAG
        )
        assert ok.status_code == 201

    resp = await client.post(
        "/api/v1/users/me/wrong-flags", headers=auth_headers, json=FLAG
    )

    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_the_admin_page_lists_flags_newest_first(
    client, auth_headers, admin_enabled
):
    await client.post("/api/v1/users/me/wrong-flags", headers=auth_headers, json=FLAG)
    await client.post(
        "/api/v1/users/me/wrong-flags",
        headers=auth_headers,
        json={
            "target": "day",
            "ref": "2026-10-15#0",
            "excerpt": "Long ride",
            "note": "Too long",
        },
    )

    resp = await client.get(
        "/api/v1/admin/wrong-flags", headers=await _admin_headers(client)
    )

    assert resp.status_code == 200
    flags = resp.json()["flags"]
    assert [f["target"] for f in flags] == ["day", "reply"]
    assert flags[0]["email"] == "rider@example.com"
    assert flags[0]["note"] == "Too long"
    assert flags[0]["gitSha"]


@pytest.mark.asyncio
async def test_the_admin_list_is_for_the_admin_only(
    client, auth_headers, admin_enabled
):
    resp = await client.get("/api/v1/admin/wrong-flags", headers=auth_headers)

    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_the_report_counts_per_week_and_says_nothing_else(client, auth_headers):
    me = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    async with TestSessionLocal() as session:
        for days_ago, note in (
            (1, "secret one"),
            (2, "secret two"),
            (9, "secret three"),
            (40, "too old"),
        ):
            session.add(
                models.WrongFlag(
                    user_id=me["id"],
                    target="reply",
                    ref="r",
                    excerpt="coach text",
                    note=note,
                    git_sha="abc",
                    created_at=now - timedelta(days=days_ago),
                )
            )
        await session.commit()
        counts = await crud.wrong_flag_weekly_counts(session, 3, now=now)

    assert [count for _, count in counts] == [0, 1, 2]
    report = wrong_flags_report.render(counts)
    assert "| 2 |" in report
    assert "secret" not in report and "coach text" not in report
