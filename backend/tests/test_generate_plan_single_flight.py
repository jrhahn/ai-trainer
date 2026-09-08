"""Concurrent plan generation is coalesced into one run (#664).

The trigger was production, 2026-09-08: two ``generate`` batches landed one
second apart and persisted two entirely different weeks, the second undoing the
first. ``useStravaSync``'s in-flight guard is a ref scoped to one hook instance,
so two browser tabs each thought they were the only caller.
"""

from __future__ import annotations

import asyncio

import pytest

from services import single_flight


# ---------------------------------------------------------------------------
# The primitive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_caller_joins_the_first_run_instead_of_starting_one():
    inflight: single_flight.InFlight = {}
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def work() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "plan"

    leader = asyncio.create_task(
        single_flight.run_single_flight(inflight, "user-1", work)
    )
    await started.wait()
    follower = asyncio.create_task(
        single_flight.run_single_flight(inflight, "user-1", work)
    )
    await asyncio.sleep(0)
    release.set()

    assert await leader == "plan"
    assert await follower == "plan"
    assert calls == 1


@pytest.mark.asyncio
async def test_different_keys_do_not_coalesce():
    """Two athletes generating at once must both get their own plan."""
    inflight: single_flight.InFlight = {}
    seen: list[str] = []

    async def work(name: str) -> str:
        seen.append(name)
        await asyncio.sleep(0)
        return name

    results = await asyncio.gather(
        single_flight.run_single_flight(inflight, "a", lambda: work("a")),
        single_flight.run_single_flight(inflight, "b", lambda: work("b")),
    )

    assert results == ["a", "b"]
    assert sorted(seen) == ["a", "b"]


@pytest.mark.asyncio
async def test_failure_reaches_the_follower_too():
    """A coalesced caller must never get a stale success in place of an error."""
    inflight: single_flight.InFlight = {}
    started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> str:
        started.set()
        await release.wait()
        raise RuntimeError("rate limited")

    leader = asyncio.create_task(
        single_flight.run_single_flight(inflight, "user-1", work)
    )
    await started.wait()
    follower = asyncio.create_task(
        single_flight.run_single_flight(inflight, "user-1", work)
    )
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(RuntimeError):
        await leader
    with pytest.raises(RuntimeError):
        await follower


@pytest.mark.asyncio
async def test_a_follower_giving_up_does_not_cancel_the_leader():
    """The leader still owes the other callers — and the database — its write."""
    inflight: single_flight.InFlight = {}
    started = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def work() -> str:
        nonlocal finished
        started.set()
        await release.wait()
        finished = True
        return "plan"

    leader = asyncio.create_task(
        single_flight.run_single_flight(inflight, "user-1", work)
    )
    await started.wait()
    follower = asyncio.create_task(
        single_flight.run_single_flight(inflight, "user-1", work)
    )
    await asyncio.sleep(0)

    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower

    release.set()
    assert await leader == "plan"
    assert finished is True


@pytest.mark.asyncio
async def test_the_key_is_released_after_success_and_after_failure():
    """A leaked key would wedge the athlete's generate endpoint forever."""
    inflight: single_flight.InFlight = {}

    async def ok() -> str:
        return "plan"

    async def boom() -> str:
        raise RuntimeError("nope")

    await single_flight.run_single_flight(inflight, "user-1", ok)
    assert inflight == {}

    with pytest.raises(RuntimeError):
        await single_flight.run_single_flight(inflight, "user-1", boom)
    assert inflight == {}

    # And the next caller runs for real rather than joining a dead future.
    assert await single_flight.run_single_flight(inflight, "user-1", ok) == "plan"


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_concurrent_generate_requests_call_the_llm_once(
    client, auth_headers, mock_ai_service
):
    from routers import ai as ai_router

    ai_router._generate_plan_inflight.clear()
    release = asyncio.Event()
    plan = [
        {
            "date": "2026-04-10",
            "workoutType": "endurance",
            "title": "Endurance Ride",
            "description": "Steady aerobic ride",
            "durationMinutes": 90,
        }
    ]

    async def slow_generate(*args, **kwargs):
        await release.wait()
        return plan

    mock_ai_service["generate_training_plan"].side_effect = slow_generate

    first = asyncio.create_task(
        client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    )
    second = asyncio.create_task(
        client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    )
    # Let both requests reach the single-flight gate before the work completes.
    for _ in range(50):
        await asyncio.sleep(0)
    release.set()

    first_response, second_response = await asyncio.gather(first, second)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json() == second_response.json()
    assert mock_ai_service["generate_training_plan"].await_count == 1


@pytest.mark.asyncio
async def test_generate_still_works_normally_when_nothing_is_in_flight(
    client, auth_headers, mock_ai_service
):
    """Coalescing must not change the single-caller behaviour."""
    from routers import ai as ai_router

    ai_router._generate_plan_inflight.clear()

    response = await client.post(
        "/api/v1/ai/generate-plan", headers=auth_headers, json={}
    )

    assert response.status_code == 200
    assert response.json()[0]["title"] == "Endurance Ride"
    assert ai_router._generate_plan_inflight == {}
