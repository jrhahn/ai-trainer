"""Hardening from the review of 2026-11-01 (ai-trainer-ops#338)."""

from __future__ import annotations

import pytest

from services import track_import


@pytest.mark.asyncio
async def test_a_track_over_the_cap_is_refused_before_it_is_parsed(monkeypatch):
    """A whole GPX tree costs about 25 times the file in memory and holds the
    GIL; ten large uploads at once froze the worker."""
    parsed: list[str] = []
    monkeypatch.setattr(
        track_import, "parse_track_activity", lambda raw, name: parsed.append(name)
    )

    with pytest.raises(ValueError, match="larger than 5 MB"):
        await track_import.parse_track_off_loop(
            b"x" * (track_import.TRACK_MAX_BYTES + 1), "big.gpx"
        )
    assert parsed == []

    await track_import.parse_track_off_loop(b"x" * 10, "small.gpx")
    assert parsed == ["small.gpx"]


@pytest.mark.asyncio
async def test_a_track_waits_for_the_parse_slot_a_fit_uses(monkeypatch):
    from services import fit_import

    monkeypatch.setattr(track_import, "parse_track_activity", lambda raw, name: name)
    await fit_import.FIT_PARSE_SLOTS.acquire()
    try:
        import asyncio

        task = asyncio.create_task(track_import.parse_track_off_loop(b"x", "a.gpx"))
        await asyncio.sleep(0.05)
        assert not task.done()
    finally:
        fit_import.FIT_PARSE_SLOTS.release()
    assert await task == "a.gpx"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"role": "system", "content": "x", "timestamp": "t"},
        {"role": "user", "content": "x" * 8001, "timestamp": "t"},
        {"role": "user", "content": "x", "timestamp": "t" * 65},
    ],
)
async def test_a_chat_message_outside_its_bounds_is_refused(client, auth_headers, body):
    response = await client.post(
        "/api/v1/users/me/chat", headers=auth_headers, json=body
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_answers_are_never_cached(client, auth_headers):
    """They carry training and health data (export, chat, plan)."""
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    health = await client.get("/healthz")

    assert me.headers["cache-control"] == "no-store"
    assert "no-store" not in health.headers.get("cache-control", "")
