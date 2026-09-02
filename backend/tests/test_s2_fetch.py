"""Retrying a throttled Semantic Scholar call instead of losing the query.

A 429 used to cost that query's papers outright — one warning, an empty list, and
the topic was simply absent from the corpus for the run. That is how an ingest
came back with 28 sources instead of 77 (#632).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts import ingest_cycling_science as ing


def _response(status: int, *, data: list | None = None, retry_after: str | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": retry_after} if retry_after else {}
    resp.json = MagicMock(return_value={"data": data if data is not None else []})
    return resp


def _client(*responses):
    """An httpx client returning *responses* in order (exceptions are raised)."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(responses))
    return client


@pytest.fixture(autouse=True)
def _no_real_sleeping():
    """Assert on the delays rather than living through them."""
    with patch.object(ing.asyncio, "sleep", new_callable=AsyncMock) as sleep:
        yield sleep


# ---------------------------------------------------------------------------
# _retry_delay / _is_retryable
# ---------------------------------------------------------------------------


def test_the_backoff_grows():
    assert ing._retry_delay(1, None) == ing.S2_BACKOFF_BASE_SECONDS
    assert ing._retry_delay(2, None) == ing.S2_BACKOFF_BASE_SECONDS * 2
    assert ing._retry_delay(3, None) > ing._retry_delay(2, None)


def test_the_server_gets_the_last_word_on_how_long_to_wait():
    """Guessing two seconds when it asked for twenty spends an attempt on a refusal."""
    assert ing._retry_delay(1, "20") == 20.0


def test_an_unparseable_retry_after_falls_back_rather_than_raising():
    # An HTTP-date is legal there and not handled; being polite beats being exact.
    assert ing._retry_delay(1, "Wed, 02 Sep 2026 10:00:00 GMT") == ing.S2_BACKOFF_BASE_SECONDS
    assert ing._retry_delay(1, "") == ing.S2_BACKOFF_BASE_SECONDS


def test_no_single_query_can_park_the_ingest():
    assert ing._retry_delay(1, "600") == ing.S2_BACKOFF_CAP_SECONDS
    assert ing._retry_delay(99, None) == ing.S2_BACKOFF_CAP_SECONDS


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_throttling_and_server_faults_are_worth_asking_again(status):
    assert ing._is_retryable(status) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_bad_request_is_not(status):
    """Repeating it only spends the allowance we are short of."""
    assert ing._is_retryable(status) is False


# ---------------------------------------------------------------------------
# _fetch_s2_papers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_throttled_query_is_retried_and_still_lands():
    client = _client(_response(429), _response(200, data=[{"paperId": "p1"}]))

    papers = await ing._fetch_s2_papers("polarized training", client)

    assert papers == [{"paperId": "p1"}]
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_the_wait_the_server_asked_for_is_the_wait_taken(_no_real_sleeping):
    client = _client(_response(429, retry_after="7"), _response(200, data=[{"paperId": "p"}]))

    await ing._fetch_s2_papers("critical power", client)

    _no_real_sleeping.assert_awaited_once_with(7.0)


@pytest.mark.asyncio
async def test_retrying_gives_up_rather_than_hammering():
    client = _client(*[_response(429) for _ in range(ing.S2_MAX_ATTEMPTS + 2)])

    papers = await ing._fetch_s2_papers("sweet spot", client)

    assert papers == []
    assert client.get.await_count == ing.S2_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_a_404_is_not_retried():
    client = _client(_response(404), _response(200, data=[{"paperId": "never"}]))

    papers = await ing._fetch_s2_papers("nonsense", client)

    assert papers == []
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_a_transport_fault_is_as_transient_as_a_503():
    client = _client(ConnectionError("reset by peer"), _response(200, data=[{"paperId": "p"}]))

    papers = await ing._fetch_s2_papers("hrv", client)

    assert papers == [{"paperId": "p"}]
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_a_persistent_transport_fault_still_gives_up():
    """Otherwise a bug in our own request would loop here forever."""
    client = _client(*[ConnectionError("down") for _ in range(ing.S2_MAX_ATTEMPTS + 2)])

    papers = await ing._fetch_s2_papers("hiit", client)

    assert papers == []
    assert client.get.await_count == ing.S2_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_a_first_time_success_costs_no_extra_call_or_sleep(_no_real_sleeping):
    client = _client(_response(200, data=[{"paperId": "p"}]))

    papers = await ing._fetch_s2_papers("vo2max", client)

    assert papers == [{"paperId": "p"}]
    assert client.get.await_count == 1
    _no_real_sleeping.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_api_key_is_sent_when_configured():
    client = _client(_response(200))

    with patch.object(ing, "S2_API_KEY", "secret-key"):
        await ing._fetch_s2_papers("ftp", client)

    assert client.get.await_args.kwargs["headers"]["x-api-key"] == "secret-key"


@pytest.mark.asyncio
async def test_no_key_header_is_invented_when_there_is_no_key():
    client = _client(_response(200))

    with patch.object(ing, "S2_API_KEY", ""):
        await ing._fetch_s2_papers("ftp", client)

    assert "x-api-key" not in client.get.await_args.kwargs["headers"]
