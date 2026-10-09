"""What the upload accepts beyond a bare .fit, and what it refuses (ai-trainer-ops#48).

Garmin Connect's "Export Original" hands the athlete a ``.zip``; Strava, Komoot
and most apps export ``.gpx``/``.tcx``. "Only .fit files are accepted" was the
first thing either kind of athlete read. A zip is now unpacked — with every
size checked against what it *unpacks* to, because a zip's sizes are its own
claims — and GPX/TCX are refused with the way forward rather than a rule.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from config import settings

BULK = "/api/v1/users/me/upload-fit/bulk"


class _Field:
    def __init__(self, name, value):
        self.name, self.value = name, value


class _Record:
    def __init__(self, fields):
        self._fields = fields

    def __iter__(self):
        return iter(self._fields)


class _FitFile:
    """Stands in for fitparse: the bytes name the device, so files differ."""

    def __init__(self, raw_stream, *args, **kwargs):
        self.raw = raw_stream.read()

    def parse(self):
        if self.raw == b"invalid":
            raise RuntimeError("invalid FIT")

    def get_messages(self, msg_type):
        # A different day per distinct content, so two files are never near
        # duplicates of each other by accident.
        start = datetime(2026, 1, 1, 8, tzinfo=timezone.utc) + timedelta(days=sum(self.raw) % 300)
        if msg_type == "file_id":
            return [_Record([_Field("serial_number", self.raw.decode(errors="replace")),
                             _Field("time_created", start)])]
        if msg_type == "session":
            return [_Record([_Field("sport", "cycling"),
                             _Field("total_elapsed_time", 1800),
                             _Field("start_time", start)])]
        return []


@pytest.fixture(autouse=True)
def fake_fitparse(monkeypatch, mock_ai_service):
    import fitparse

    monkeypatch.setattr(fitparse, "FitFile", _FitFile)


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


async def _upload(client, headers, *files: tuple[str, bytes]) -> dict:
    response = await client.post(
        BULK,
        headers=headers,
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_garmin_export_zip_imports_its_fit(client: AsyncClient, auth_headers):
    body = await _upload(client, auth_headers, ("activity_1.zip", _zip({"activity_1.fit": b"ride-a"})))

    assert body["imported"] == 1
    assert body["files"][0]["filename"] == "activity_1.zip › activity_1.fit"
    # The day it happened, so the result can link there.
    assert body["files"][0]["activityDate"].startswith("2026-")


async def test_every_fit_in_a_zip_is_its_own_import(client: AsyncClient, auth_headers):
    archive = _zip({"a.fit": b"ride-a", "nested/b.fit": b"ride-b", "readme.txt": b"hi"})
    body = await _upload(client, auth_headers, ("export.zip", archive))

    assert [f["filename"] for f in body["files"]] == ["export.zip › a.fit", "export.zip › b.fit"]
    assert body["imported"] == 2


async def test_each_fit_in_a_zip_pays_its_own_ai_call(client: AsyncClient, auth_headers, monkeypatch):
    """Otherwise a zip would be a way to buy AI calls at a flat rate of one."""
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 1)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    body = await _upload(client, auth_headers, ("two.zip", _zip({"a.fit": b"ride-a", "b.fit": b"ride-b"})))

    assert body["files"][0]["status"] == "imported"
    assert "rate limit" in body["files"][1]["message"].lower()


async def test_files_after_the_limit_are_named_not_dropped(client: AsyncClient, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 1)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    body = await _upload(
        client, auth_headers,
        ("two.zip", _zip({"a.fit": b"ride-a", "b.fit": b"ride-b"})),
        ("later.fit", b"ride-c"),
    )

    assert [f["filename"] for f in body["files"]] == ["two.zip › a.fit", "two.zip › b.fit", "later.fit"]
    assert all("rate limit" in f["message"].lower() for f in body["files"][1:])


@pytest.mark.parametrize("name", ["morning.gpx", "Morning.TCX"])
async def test_gpx_and_tcx_are_refused_with_the_way_forward(client: AsyncClient, auth_headers, name):
    body = await _upload(client, auth_headers, (name, b"<gpx/>"))

    message = body["files"][0]["message"]
    assert body["files"][0]["status"] == "failed"
    assert "Export Original" in message
    assert "Add activity" in message


async def test_anything_else_is_refused_plainly(client: AsyncClient, auth_headers):
    body = await _upload(client, auth_headers, ("photo.jpg", b"\xff\xd8"))
    assert body["files"][0]["message"] == "Only .fit files, or a .zip of them, are accepted"


async def test_a_zip_without_a_fit_says_so(client: AsyncClient, auth_headers):
    body = await _upload(client, auth_headers, ("notes.zip", _zip({"readme.txt": b"hi"})))
    assert "contains no .fit" in body["files"][0]["message"]


async def test_a_file_that_is_not_a_zip_says_so(client: AsyncClient, auth_headers):
    body = await _upload(client, auth_headers, ("fake.zip", b"not a zip"))
    assert "not a readable .zip" in body["files"][0]["message"]


async def test_a_bad_fit_inside_a_zip_fails_alone(client: AsyncClient, auth_headers):
    archive = _zip({"good.fit": b"ride-a", "bad.fit": b"invalid"})
    body = await _upload(client, auth_headers, ("mixed.zip", archive))

    assert [f["status"] for f in body["files"]] == ["imported", "failed"]


# ---------------------------------------------------------------------------
# A zip's sizes are its own claims
# ---------------------------------------------------------------------------


async def test_a_member_over_the_file_limit_is_refused(client: AsyncClient, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "fit_upload_max_bytes", 1000)
    body = await _upload(client, auth_headers, ("big.zip", _zip({"big.fit": b"\0" * 5000})))

    assert body["files"][0]["status"] == "failed"
    assert "larger than the limit" in body["files"][0]["message"]


async def test_a_bomb_of_small_members_is_refused_by_its_total(client: AsyncClient, auth_headers, monkeypatch):
    """Each member under the limit, the archive tiny, the unpacked total not."""
    monkeypatch.setattr(settings, "fit_upload_max_bytes", 2000)
    members = {f"m{i}.fit": b"\0" * 1900 for i in range(10)}  # 19 000 > 5 × 2 000
    body = await _upload(client, auth_headers, ("bomb.zip", _zip(members)))

    assert body["files"][0]["status"] == "failed"
    assert "unpacks to more than" in body["files"][0]["message"]


async def test_a_zip_that_lies_about_its_sizes_is_still_bounded(client: AsyncClient, auth_headers, monkeypatch):
    """The read is capped by the limit, not by the header's claim."""
    monkeypatch.setattr(settings, "fit_upload_max_bytes", 1000)
    archive = bytearray(_zip({"liar.fit": b"\0" * 5000}))
    # Rewrite the declared uncompressed size (central directory and local
    # header) to something harmless; the real stream still inflates to 5 000.
    real = (5000).to_bytes(4, "little")
    fake = (100).to_bytes(4, "little")
    archive = bytes(archive).replace(real, fake)
    body = await _upload(client, auth_headers, ("liar.zip", archive))

    assert body["files"][0]["status"] == "failed"
    assert "damaged" in body["files"][0]["message"]


async def test_more_members_than_a_batch_is_refused(client: AsyncClient, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "fit_upload_bulk_max_files", 2)
    archive = _zip({f"r{i}.fit": b"ride" for i in range(3)})
    body = await _upload(client, auth_headers, ("many.zip", archive))

    assert "more than 2" in body["files"][0]["message"]


def _with_member_header(archive: bytes, *, flag_bits: int | None = None, method: int | None = None) -> bytes:
    """Rewrite the member's local and central headers: the stdlib cannot write
    an encrypted zip, nor one in a compression method it lacks."""
    data = bytearray(archive)
    for signature, flag_offset, method_offset in ((b"PK\x03\x04", 6, 8), (b"PK\x01\x02", 8, 10)):
        at = data.find(signature)
        if flag_bits is not None:
            current = int.from_bytes(data[at + flag_offset:at + flag_offset + 2], "little")
            data[at + flag_offset:at + flag_offset + 2] = (current | flag_bits).to_bytes(2, "little")
        if method is not None:
            data[at + method_offset:at + method_offset + 2] = method.to_bytes(2, "little")
    return bytes(data)


async def test_a_password_protected_zip_says_so(client: AsyncClient, auth_headers):
    archive = _with_member_header(_zip({"ride.fit": b"ride-a"}), flag_bits=0x1)
    body = await _upload(client, auth_headers, ("locked.zip", archive))

    assert body["files"][0]["status"] == "failed"
    assert "password-protected" in body["files"][0]["message"]


async def test_a_compression_method_zipfile_lacks_fails_that_file_only(
    client: AsyncClient, auth_headers
):
    archive = _with_member_header(_zip({"ride.fit": b"ride-a"}), method=99)
    body = await _upload(client, auth_headers, ("odd.zip", archive), ("next.fit", b"ride-b"))

    assert [f["status"] for f in body["files"]] == ["failed", "imported"]
    assert "damaged" in body["files"][0]["message"]


async def test_a_file_that_yields_nothing_spends_no_ai_call(
    client: AsyncClient, auth_headers, monkeypatch
):
    """Only an attempted import costs one. A broken zip in front of two rides
    leaves the request's own charge for the first ride, so both fit in a
    burst of two."""
    monkeypatch.setattr(settings, "ai_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_rate_limit_burst", 2)
    monkeypatch.setattr(settings, "ai_rate_limit_sustained", 100)

    body = await _upload(
        client, auth_headers,
        ("broken.zip", b"not a zip"), ("a.fit", b"ride-a"), ("b.fit", b"ride-b"),
    )

    assert [f["status"] for f in body["files"]] == ["failed", "imported", "imported"]


async def test_a_skipped_file_still_names_its_day(client: AsyncClient, auth_headers):
    """Both skip paths — a duplicate within the batch, and one already
    imported before — say which day, so their result can link there too."""
    first = await _upload(client, auth_headers, ("a.fit", b"ride-a"), ("again.fit", b"ride-a"))
    later = await _upload(client, auth_headers, ("a-later.fit", b"ride-a"))

    day = first["files"][0]["activityDate"]
    assert first["files"][1]["status"] == "skipped"
    assert first["files"][1]["activityDate"] == day
    assert later["files"][0]["status"] == "skipped"
    assert later["files"][0]["activityDate"] == day

