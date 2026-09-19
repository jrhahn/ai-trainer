"""Body-size limits on uploads and on requests generally (#682).

Two layers with different strengths, which is why they are tested apart:

- the .fit routes cap themselves *while reading*, so an oversized file is never
  fully buffered. That is the guarantee.
- the global middleware rejects from ``Content-Length`` before the body is
  read. That is a backstop for every other route, and it is weaker on purpose:
  a chunked request carries no ``Content-Length`` and slips past it.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from config import settings

_UPLOAD = "/api/v1/users/me/upload-fit"
_BULK = "/api/v1/users/me/upload-fit/bulk"


def _fit(name: str, size: int) -> tuple[str, tuple[str, bytes, str]]:
    return (
        "files",
        (name, b"x" * size, "application/octet-stream"),
    )


# ---------------------------------------------------------------------------
# Per-file cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_oversized_fit_upload_is_refused(
    client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "fit_upload_max_bytes", 1024)

    resp = await client.post(
        _UPLOAD,
        headers=auth_headers,
        files={"file": ("ride.fit", b"x" * 4096, "application/octet-stream")},
    )

    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_a_file_under_the_cap_still_reaches_the_parser(
    client: AsyncClient, auth_headers, monkeypatch
):
    """The cap must not become a blanket refusal.

    A 422 here means the bytes got all the way to the .fit parser and were
    rejected on their contents, which is the behaviour this route had before.
    """
    monkeypatch.setattr(settings, "fit_upload_max_bytes", 1024 * 1024)

    resp = await client.post(
        _UPLOAD,
        headers=auth_headers,
        files={"file": ("ride.fit", b"not really a fit file", "application/octet-stream")},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_one_oversized_file_fails_its_own_entry_not_the_batch(
    client: AsyncClient, auth_headers, monkeypatch
):
    """Same treatment an unparseable file already got.

    Failing the whole request would throw away the files that were fine, which
    for a bulk import of a season's rides is the wrong trade.
    """
    monkeypatch.setattr(settings, "fit_upload_max_bytes", 1024)

    resp = await client.post(
        _BULK,
        headers=auth_headers,
        files=[_fit("small.fit", 64), _fit("huge.fit", 4096)],
    )

    assert resp.status_code == 200
    results = resp.json()["files"]
    assert len(results) == 2, results
    assert "limit" in results[1]["message"].lower(), results


# ---------------------------------------------------------------------------
# Batch size cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_too_many_files_in_one_batch_is_refused(
    client: AsyncClient, auth_headers, monkeypatch
):
    """Each file is an LLM call, so the batch size is a spend multiplier."""
    monkeypatch.setattr(settings, "fit_upload_bulk_max_files", 2)

    resp = await client.post(
        _BULK,
        headers=auth_headers,
        files=[_fit(f"ride{i}.fit", 64) for i in range(3)],
    )

    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_a_batch_at_the_cap_is_accepted(
    client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "fit_upload_bulk_max_files", 2)

    resp = await client.post(
        _BULK,
        headers=auth_headers,
        files=[_fit(f"ride{i}.fit", 64) for i in range(2)],
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Global middleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_declaring_an_oversized_body_is_refused(
    client: AsyncClient, auth_headers, monkeypatch
):
    """Rejected from the declared length, so the body is never read."""
    monkeypatch.setattr(settings, "max_request_body_bytes", 128)

    resp = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"name": "x" * 4096},
    )

    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_a_normal_request_is_unaffected(
    client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "max_request_body_bytes", 64 * 1024 * 1024)

    resp = await client.get("/api/v1/users/me", headers=auth_headers)

    assert resp.status_code == 200
