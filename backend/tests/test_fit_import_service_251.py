"""The .fit import lives in a service, and the routers reach it there (ai-trainer-ops#251).

Some 600 lines of routers/users.py were the FIT subsystem, and routers/tools.py
imported three of its private helpers from there. The service raises its own
errors for the two things that stop an upload before parsing; the routers turn
them into 503 and 413.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import dependencies
from services import fit_import

BACKEND = Path(__file__).resolve().parents[1]


def test_no_router_imports_from_the_users_router():
    found = []
    for path in sorted((BACKEND / "routers").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "routers.users":
                found.append(path.name)
            elif isinstance(node, ast.Import) and any(
                alias.name == "routers.users" for alias in node.names
            ):
                found.append(path.name)

    assert found == []


def test_the_service_knows_nothing_of_http():
    source = (BACKEND / "services" / "fit_import.py").read_text()
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not {name for name in imported if name and name.startswith("fastapi")}
    assert "HTTPException" not in source


class _Upload:
    """Just the async ``read(size)`` an upload offers."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


@pytest.mark.asyncio
async def test_an_upload_over_the_cap_is_refused_before_it_is_all_read(monkeypatch):
    monkeypatch.setattr(fit_import.settings, "fit_upload_max_bytes", 10)

    assert await fit_import.read_upload_capped(_Upload(b"x" * 10), "ride.fit") == (
        b"x" * 10
    )
    with pytest.raises(fit_import.UploadTooLarge, match="ride.fit is larger than"):
        await fit_import.read_upload_capped(_Upload(b"x" * 11), "ride.fit")
    with pytest.raises(HTTPException) as refused:
        await dependencies.read_upload_capped(_Upload(b"x" * 11), "ride.fit")
    assert refused.value.status_code == 413


def test_a_missing_parser_library_is_the_services_error_and_a_503(monkeypatch):
    real_import = builtins.__import__

    def without_fitparse(name, *args, **kwargs):
        if name == "fitparse":
            raise ImportError("no fitparse here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_fitparse)

    with pytest.raises(fit_import.FitParserUnavailable):
        fit_import.fit_file_parser()
    with pytest.raises(HTTPException) as refused:
        dependencies.fit_file_parser_or_503()
    assert refused.value.status_code == 503
    assert "fitparse" in refused.value.detail


# --- The parts the routes run in a worker thread --------------------------------
# Coverage does not follow asyncio.to_thread, so the parse and the unpacking
# looked untested although every upload test runs them. These call them directly.

import io  # noqa: E402
import zipfile  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402


class _Message:
    def __init__(self, **fields) -> None:
        self._fields = [SimpleNamespace(name=k, value=v) for k, v in fields.items()]

    def __iter__(self):
        return iter(self._fields)


def _fit_file(**messages):
    class FitFile:
        def __init__(self, _stream) -> None:
            pass

        def parse(self) -> None:
            pass

        def get_messages(self, message_type):
            return list(messages.get(message_type, []))

    return FitFile


START = datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)


def test_a_ride_with_every_stream_is_read_whole():
    records = [
        _Message(
            timestamp=START + timedelta(seconds=t),
            power=200 + t,
            heart_rate=140,
            cadence=90,
            enhanced_altitude=310.256,
            speed=8.5,
            # Semicircles: the device's own unit for a position.
            position_lat=int(50.1 / (180.0 / 2**31)),
            position_long=int(8.6 / (180.0 / 2**31)),
        )
        for t in range(3)
    ]
    FitFile = _fit_file(
        file_id=[_Message(serial_number=42, time_created=START, manufacturer="garmin")],
        session=[_Message(sport="cycling", start_time=START)],
        record=[_Message(timestamp=None), *records],
    )

    parsed = fit_import.parse_fit_activity(b"", "ride.fit", FitFile)

    assert parsed.sport_type == "cycling"
    # No elapsed time in the session: the records' span stands in.
    assert parsed.duration_seconds == 2
    assert parsed.streams["cadence"]["data"] == [90, 90, 90]
    assert parsed.streams["altitude"]["data"] == [310.26] * 3
    assert parsed.streams["latlng"]["data"][0] == [
        pytest.approx(50.1, abs=1e-5),
        pytest.approx(8.6, abs=1e-5),
    ]
    assert (parsed.start_lat, parsed.start_lng) == tuple(
        parsed.streams["latlng"]["data"][0]
    )
    assert parsed.source_metadata["file_id_serial_number"] == 42
    assert parsed.source_metadata["file_id_time_created"] == START.isoformat()


def test_a_file_the_library_cannot_read_is_refused():
    class Broken:
        def __init__(self, _stream) -> None:
            raise RuntimeError("not a fit file")

    with pytest.raises(ValueError, match="Could not parse"):
        fit_import.parse_fit_activity(b"junk", "junk.fit", Broken)


def test_the_field_helpers_read_what_a_device_may_send():
    assert fit_import._as_int("12") == 12
    assert fit_import._as_int("twelve") is None
    assert fit_import._as_float("1.5") == 1.5
    assert fit_import._as_float(object()) is None
    assert fit_import._semicircles_to_degrees(None) is None
    assert fit_import._json_safe_fit_value(b"\x01") == str(b"\x01")


def _zip(*members: tuple[str, bytes], password_flag: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    raw = bytearray(buffer.getvalue())
    if password_flag:
        # The "encrypted" bit of the general purpose flags, in the local and the
        # central header alike: zipfile writes no encrypted archive itself.
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            at = raw.find(signature)
            raw[at + offset] |= 0x1
    return bytes(raw)


def test_a_zip_gives_its_fit_files_and_nothing_else():
    payloads = fit_import.fit_payloads_from_zip(
        "export.zip",
        _zip(("rides/one.fit", b"1"), ("notes.txt", b"x"), ("two.FIT", b"2")),
    )

    assert payloads == [("export.zip › one.fit", b"1"), ("export.zip › two.FIT", b"2")]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not a zip", "is not a readable .zip file"),
        (_zip(("notes.txt", b"x")), "contains no .fit file"),
        (_zip(("ride.fit", b"1"), password_flag=True), "is password-protected"),
    ],
)
def test_a_zip_that_cannot_be_used_says_why(raw, message):
    with pytest.raises(ValueError, match=message):
        fit_import.fit_payloads_from_zip("export.zip", raw)


def test_a_zip_is_held_to_the_upload_limits(monkeypatch):
    monkeypatch.setattr(fit_import.settings, "fit_upload_bulk_max_files", 2)
    with pytest.raises(ValueError, match="holds more than 2"):
        fit_import.fit_payloads_from_zip(
            "export.zip", _zip(("a.fit", b"1"), ("b.fit", b"2"), ("c.fit", b"3"))
        )

    monkeypatch.setattr(fit_import.settings, "fit_upload_bulk_max_files", 10)
    monkeypatch.setattr(fit_import.settings, "fit_upload_max_bytes", 4)
    with pytest.raises(ValueError, match="larger than the limit"):
        fit_import.fit_payloads_from_zip("export.zip", _zip(("a.fit", b"12345")))
    # Each member within the limit, together past five files' worth.
    with pytest.raises(ValueError, match="unpacks to more than the upload limit"):
        fit_import.fit_payloads_from_zip(
            "export.zip", _zip(*[(f"{n}.fit", b"1234") for n in range(6)])
        )


def test_a_damaged_member_is_that_files_failure():
    raw = bytearray(_zip(("ride.fit", b"power " * 200)))
    # A byte of the compressed stream, just past the local header (30 bytes and
    # the name): the member no longer matches its own checksum.
    raw[30 + len("ride.fit") + 2] ^= 0xFF

    with pytest.raises(ValueError, match="ride.fit in export.zip is damaged"):
        fit_import.fit_payloads_from_zip("export.zip", bytes(raw))
