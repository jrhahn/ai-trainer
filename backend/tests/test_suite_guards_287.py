"""The suite's own guards (ai-trainer-ops#287): no test reaches the network,
and passwords hash at a cheap cost unless a test asks for the real one."""

from __future__ import annotations

import socket

import argon2
import httpx
import pytest

from tests import conftest
from domain import passwords

UNROUTED = "203.0.113.7"  # TEST-NET-3: documentation only, never routed


def test_a_connection_beyond_this_machine_is_refused_and_counted():
    with socket.socket() as sock, pytest.raises(RuntimeError, match="mock the call"):
        sock.connect((UNROUTED, 443))

    assert conftest._network_attempts == [f"{UNROUTED}:443"]
    conftest._network_attempts.clear()  # made on purpose, so not this test's failure


@pytest.mark.asyncio
async def test_an_http_client_is_refused_as_well():
    async with httpx.AsyncClient() as client:
        with pytest.raises(Exception):  # noqa: B017 - the refusal, however the client wraps it
            await client.get(f"http://{UNROUTED}/")

    assert conftest._network_attempts == [f"{UNROUTED}:80"]
    conftest._network_attempts.clear()


def test_a_refusal_the_code_swallowed_still_fails_the_test():
    try:
        socket.create_connection((UNROUTED, 443), timeout=1)
    except Exception:  # noqa: BLE001 - what a fallback in the code under test does
        pass

    with pytest.raises(pytest.fail.Exception, match=f"{UNROUTED}:443"):
        conftest.fail_on_network_attempts()
    assert conftest._network_attempts == []


def test_loopback_and_unix_sockets_stay_open():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with socket.create_connection(server.getsockname(), timeout=2):
            pass
    left, right = socket.socketpair()
    left.close()
    right.close()

    assert conftest._network_attempts == []


def test_the_suite_hashes_passwords_cheaply():
    params = argon2.extract_parameters(passwords.hash_password("Str0ng!Pass"))

    assert (params.time_cost, params.memory_cost, params.parallelism) == (1, 8192, 1)


@pytest.mark.production_hashing
def test_the_app_hashes_at_argon2s_own_cost():
    hashed = passwords.hash_password("Str0ng!Pass")
    params = argon2.extract_parameters(hashed)
    library = argon2.PasswordHasher()

    assert (params.time_cost, params.memory_cost, params.parallelism) == (
        library.time_cost,
        library.memory_cost,
        library.parallelism,
    )
    assert params.time_cost >= 3 and params.memory_cost >= 64 * 1024
    assert passwords.verify_password("Str0ng!Pass", hashed)
